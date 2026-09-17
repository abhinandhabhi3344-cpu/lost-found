import os
import numpy as np
from PIL import Image

HAS_FAISS = False
HAS_CLIP = False
clip_model = None
clip_processor = None
faiss_module = None

# Similarity contract (percentages shown in the UI):
#   - min_threshold (default 60.0): minimum score for a case to be returned.
#   - HIGH_MATCH_THRESHOLD (80.0): score at/above which a match is "high".
# Cosine similarity maps directly to percent (0.78 -> 78.0%).
DEFAULT_MIN_THRESHOLD = 60.0
HIGH_MATCH_THRESHOLD = 80.0

# Geometric test-time augmentations used to match the SAME subject
# photographed from a different angle / framing / orientation.
# Similarity is max-pooled over every query-variant x db-variant pair,
# so a rotated / mirrored / re-framed photo still matches.
TTA_ANGLES = (15, -15, 30, -30)
TTA_CROP_RATIO = 0.80

# Bounded in-process embedding cache: (backend, path, mtime_ns, size) -> matrix.
_EMB_CACHE = {}
_EMB_CACHE_MAX = 2000

try:
    import faiss
    faiss_module = faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

def _load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ[k.strip()] = v.strip()
        except Exception:
            pass

_load_env_file()

def load_clip_model():
    global clip_model, clip_processor, HAS_CLIP
    if HAS_CLIP and clip_model is not None:
        return True
    try:
        import torch  # noqa: F401  (required backend for CLIP inference)
        from transformers import CLIPProcessor, CLIPModel

        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        hf_token = os.environ.get("HF_TOKEN")

        # NOTE: a stale/invalid HF_TOKEN must not kill CLIP entirely —
        # openai/clip-vit-base-patch32 is public, so retry without a token.
        load_kwargs = {"token": hf_token} if hf_token else {}
        try:
            clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", **load_kwargs)
            clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32", **load_kwargs)
        except Exception:
            clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        clip_model.eval()
        HAS_CLIP = True
        return True
    except Exception:
        clip_model = None
        clip_processor = None
        HAS_CLIP = False
        return False


def _l2norm_rows(mat):
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def extract_fallback_vector(pil_img, dim=512):
    """
    512-dimensional L2-normalized visual descriptor.
    Histograms are densities (size invariant); spatial grid + edge
    orientation features are matched with test-time augmentation
    (see _variant_images) so different camera angles still match.
    """
    try:
        img_rgb = pil_img.convert('RGB').resize((128, 128))
        img_hsv = pil_img.convert('HSV').resize((128, 128))

        arr_rgb = np.array(img_rgb, dtype=np.float32) / 255.0
        arr_hsv = np.array(img_hsv, dtype=np.float32) / 255.0
        n_px = float(arr_rgb.shape[0] * arr_rgb.shape[1])

        # 1. Global HSV color densities (pose invariant)
        h_hist, _ = np.histogram(arr_hsv[:, :, 0], bins=32, range=(0, 1))
        s_hist, _ = np.histogram(arr_hsv[:, :, 1], bins=16, range=(0, 1))
        v_hist, _ = np.histogram(arr_hsv[:, :, 2], bins=16, range=(0, 1))
        h_hist = h_hist.astype(np.float32) / n_px
        s_hist = s_hist.astype(np.float32) / n_px
        v_hist = v_hist.astype(np.float32) / n_px

        # 2. Spatial grid RGB mean/std (pose sensitive on its own, but
        #    max-pooled over augmented variants at match time)
        grid_feats = []
        h_step, w_step = 128 // 4, 128 // 4
        for r in range(4):
            for c in range(4):
                cell = arr_rgb[r*h_step:(r+1)*h_step, c*w_step:(c+1)*w_step]
                grid_feats.extend(cell.mean(axis=(0, 1)))
                grid_feats.extend(cell.std(axis=(0, 1)))
        grid_feats = np.array(grid_feats, dtype=np.float32)

        # 3. Edge magnitude / orientation densities (largely pose robust)
        gray = np.array(pil_img.convert('L').resize((64, 64)), dtype=np.float32) / 255.0
        gy, gx = np.gradient(gray)
        magnitude = np.sqrt(gx**2 + gy**2)
        orientation = np.arctan2(gy, gx)
        mag_hist, _ = np.histogram(magnitude, bins=64, range=(0, 1))
        ori_hist, _ = np.histogram(orientation, bins=64, range=(-np.pi, np.pi))
        g_px = float(gray.size)
        mag_hist = mag_hist.astype(np.float32) / g_px
        ori_hist = ori_hist.astype(np.float32) / g_px

        # 4. Texture statistics
        texture_var = np.array([gray.var(), gx.var(), gy.var()], dtype=np.float32)

        # 5. Center-vs-border contrast (subject vs background cue)
        center_mean = arr_rgb[32:96, 32:96].mean(axis=(0, 1))
        border_mean = arr_rgb[0:32, :].mean(axis=(0, 1))
        bg_contrast = (center_mean - border_mean).astype(np.float32)

        parts = [h_hist, s_hist, v_hist, grid_feats, mag_hist, ori_hist,
                 texture_var, bg_contrast]
        flat_len = sum(len(p) for p in parts)
        padding = np.zeros(max(0, dim - flat_len), dtype=np.float32)

        vec = np.concatenate(parts + [padding])[:dim]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)
    except Exception:
        vec = np.zeros(dim, dtype=np.float32)
        vec[0] = 1.0
        return vec


def _variant_images(pil_img):
    """Original + geometric variants covering angle/framing changes."""
    img = pil_img.convert('RGB')
    w, h = img.size
    variants = [img, img.transpose(Image.FLIP_LEFT_RIGHT)]
    for angle in TTA_ANGLES:
        variants.append(img.rotate(angle, expand=True, resample=Image.BICUBIC))
    # Center crop (tighter framing) + its mirror
    mw, mh = int(w * TTA_CROP_RATIO), int(h * TTA_CROP_RATIO)
    left, top = (w - mw) // 2, (h - mh) // 2
    crop = img.crop((left, top, left + mw, top + mh))
    variants.append(crop)
    variants.append(crop.transpose(Image.FLIP_LEFT_RIGHT))
    return variants


def _open_pil(image_input):
    if isinstance(image_input, (str, os.PathLike)):
        if not os.path.exists(image_input):
            return None
        return Image.open(image_input).convert('RGB')
    return Image.open(image_input).convert('RGB')


def _clip_variant_matrix(pil_img):
    """Batched CLIP embeddings (one row per augmented variant)."""
    import torch
    variants = _variant_images(pil_img)
    inputs = clip_processor(images=variants, return_tensors="pt")
    with torch.no_grad():
        feats = clip_model.get_image_features(**inputs)
    mat = feats.cpu().numpy().astype(np.float32)
    return _l2norm_rows(mat)


def _fallback_variant_matrix(pil_img):
    return _l2norm_rows(np.array(
        [extract_fallback_vector(v) for v in _variant_images(pil_img)],
        dtype=np.float32,
    ))


def _variant_matrix_for_path(path):
    """Cached variant matrix for a database image file."""
    try:
        st = os.stat(path)
        backend = 'clip' if load_clip_model() else 'fallback'
        key = (backend, os.path.abspath(path), st.st_mtime_ns, st.st_size)
        hit = _EMB_CACHE.get(key)
        if hit is not None:
            return hit
        pil_img = Image.open(path).convert('RGB')
        if backend == 'clip':
            try:
                mat = _clip_variant_matrix(pil_img)
            except Exception:
                mat = _fallback_variant_matrix(pil_img)
        else:
            mat = _fallback_variant_matrix(pil_img)
        if len(_EMB_CACHE) >= _EMB_CACHE_MAX:
            _EMB_CACHE.clear()
        _EMB_CACHE[key] = mat
        return mat
    except Exception:
        return None


def _variant_matrix(image_input):
    """Variant matrix for a query upload (file object) or a path."""
    try:
        if isinstance(image_input, (str, os.PathLike)):
            if not os.path.exists(image_input):
                return None
            return _variant_matrix_for_path(os.fspath(image_input))
        pil_img = _open_pil(image_input)
        if pil_img is None:
            return None
        if load_clip_model():
            try:
                return _clip_variant_matrix(pil_img)
            except Exception:
                pass
        return _fallback_variant_matrix(pil_img)
    except Exception:
        return None


def get_image_embedding(image_input):
    """
    Single 512-dim embedding for one image (original framing only).
    Kept for backward compatibility; search uses _variant_matrix.
    """
    try:
        pil_img = _open_pil(image_input)
        if pil_img is None:
            return None
        if load_clip_model():
            try:
                import torch
                inputs = clip_processor(images=pil_img, return_tensors="pt")
                with torch.no_grad():
                    image_features = clip_model.get_image_features(**inputs)
                vec = image_features.cpu().numpy()[0]
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                return vec.astype(np.float32)
            except Exception:
                pass
        return extract_fallback_vector(pil_img)
    except Exception:
        return None


def _max_cosine(query_mat, db_mat):
    """Max cosine over all query-variant x db-variant pairs (exact)."""
    if HAS_FAISS and faiss_module is not None:
        try:
            index = faiss_module.IndexFlatIP(db_mat.shape[1])
            index.add(np.ascontiguousarray(db_mat, dtype=np.float32))
            D, _ = index.search(np.ascontiguousarray(query_mat, dtype=np.float32), 1)
            return float(np.max(D))
        except Exception:
            pass
    return float(np.max(np.dot(query_mat, db_mat.T)))


def search_cases_with_ai(query_image_files, all_cases, min_threshold=DEFAULT_MIN_THRESHOLD):
    """
    Queries database cases against uploaded search image(s).

    Angle handling: every image is expanded into geometric variants
    (mirror / rotations / tighter crop) on BOTH the query and database
    side, and similarity is max-pooled over all pairs — so the same dog
    photographed from a different angle still matches.

    Returns ranked [{'case', 'match_score' (0-100), 'is_high_match'}],
    keeping only cases with match_score >= min_threshold.
    """
    if not query_image_files:
        return []

    # 1. Variant matrices for the query image(s)
    query_mats = []
    for q_file in query_image_files:
        mat = _variant_matrix(q_file)
        if mat is not None:
            query_mats.append(mat)
    if not query_mats:
        return []
    query_matrix = np.vstack(query_mats).astype(np.float32)

    # 2. Compare against every case, best image wins
    try:
        gate = float(min_threshold)
    except (TypeError, ValueError):
        gate = DEFAULT_MIN_THRESHOLD

    case_results = []
    for case in all_cases:
        try:
            case_imgs = list(case.images.all())
        except Exception:
            continue
        best_sim = 0.0
        for c_img in case_imgs:
            try:
                if not c_img.image:
                    continue
                img_path = c_img.image.path
            except Exception:
                continue
            if not os.path.exists(img_path):
                continue
            db_mat = _variant_matrix_for_path(img_path)
            if db_mat is None:
                continue
            sim = _max_cosine(query_matrix, db_mat)
            if sim > best_sim:
                best_sim = sim

        if best_sim <= 0:
            continue
        percentage = round(min(1.0, max(0.0, best_sim)) * 100.0, 1)
        if percentage >= gate:
            case_results.append({
                'case': case,
                'match_score': percentage,
                'is_high_match': percentage >= HIGH_MATCH_THRESHOLD,
            })

    case_results.sort(key=lambda x: x['match_score'], reverse=True)
    return case_results
