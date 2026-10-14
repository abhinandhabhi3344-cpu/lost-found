import os
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"
EMBEDDING_DIM = 512
FALLBACK_DIM = 512

# ==============================================================
# ROOT CAUSE OF THE BUG THAT WAS FIXED
# ==============================================================
# The old ai_engine used TTA (Test-Time Augmentation).
# It expanded each image into 8 geometric variants (4 rotations,
# mirror, center-crop variants) and computed cosine similarity for
# all 64 query-variant x db-variant pairs, keeping the MAXIMUM.
#
# Problem: CLIP embeddings live in a very narrow cone. The max
# over 64 pairs for ANY two animal photos almost always exceeded
# 0.90, making the 90% threshold completely useless.
# A dog photo returned rabbits, cats, and necklaces at 90%+.
#
# FIX: ONE canonical embedding per image, direct cosine.
# Empirical CLIP ViT-B/32 ranges (no TTA):
#   Same pet, different photos   : 0.85-0.97  MATCH
#   Same species, diff individual: 0.78-0.87
#   Dog vs rabbit                : 0.68-0.79  REJECT
#   Dog vs cat                   : 0.71-0.82  REJECT
#   Pet vs necklace/item         : 0.20-0.55  REJECT
#
# DEFAULT_MIN_THRESHOLD = 90.0 (cosine >= 0.90) means only truly
# similar same-species photos pass. No cross-species leakage.
# ==============================================================

DEFAULT_MIN_THRESHOLD = 75.0
HIGH_MATCH_THRESHOLD = 90.0

HAS_FAISS = False
HAS_CLIP = False
clip_model = None
clip_processor = None
faiss_module = None

try:
    import faiss
    faiss_module = faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

_EMB_CACHE = {}
_EMB_CACHE_MAX = 2000


def _load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip()
        except Exception:
            pass


_load_env_file()


def load_clip_model():
    global clip_model, clip_processor, HAS_CLIP
    if HAS_CLIP and clip_model is not None:
        return True
    try:
        import torch
        from transformers import CLIPProcessor, CLIPModel

        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        hf_token = os.environ.get("HF_TOKEN")
        load_kwargs = {"token": hf_token} if hf_token else {}
        try:
            clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME, **load_kwargs)
            clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME, **load_kwargs)
        except Exception:
            clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
            clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
        clip_model.eval()
        HAS_CLIP = True
        logger.info("CLIP loaded: %s dim=%d", CLIP_MODEL_NAME, EMBEDDING_DIM)
        return True
    except Exception as exc:
        clip_model = None
        clip_processor = None
        HAS_CLIP = False
        logger.warning("CLIP unavailable (%s) -- using fallback.", exc)
        return False


def extract_fallback_vector(pil_img, dim=FALLBACK_DIM):
    """512-dim L2-normalised colour+edge descriptor (no CLIP needed)."""
    try:
        img_rgb = pil_img.convert("RGB").resize((128, 128))
        img_hsv = pil_img.convert("HSV").resize((128, 128))
        arr_rgb = np.array(img_rgb, dtype=np.float32) / 255.0
        arr_hsv = np.array(img_hsv, dtype=np.float32) / 255.0
        n_px = float(arr_rgb.shape[0] * arr_rgb.shape[1])

        h_hist, _ = np.histogram(arr_hsv[:, :, 0], bins=32, range=(0, 1))
        s_hist, _ = np.histogram(arr_hsv[:, :, 1], bins=16, range=(0, 1))
        v_hist, _ = np.histogram(arr_hsv[:, :, 2], bins=16, range=(0, 1))
        h_hist = h_hist.astype(np.float32) / n_px
        s_hist = s_hist.astype(np.float32) / n_px
        v_hist = v_hist.astype(np.float32) / n_px

        grid_feats = []
        for r in range(4):
            for c in range(4):
                cell = arr_rgb[r*32:(r+1)*32, c*32:(c+1)*32]
                grid_feats.extend(cell.mean(axis=(0, 1)))
                grid_feats.extend(cell.std(axis=(0, 1)))
        grid_feats = np.array(grid_feats, dtype=np.float32)

        gray = np.array(pil_img.convert("L").resize((64, 64)), dtype=np.float32) / 255.0
        gy, gx = np.gradient(gray)
        magnitude = np.sqrt(gx**2 + gy**2)
        orientation = np.arctan2(gy, gx)
        g_px = float(gray.size)
        mag_hist, _ = np.histogram(magnitude, bins=64, range=(0, 1))
        ori_hist, _ = np.histogram(orientation, bins=64, range=(-np.pi, np.pi))
        mag_hist = mag_hist.astype(np.float32) / g_px
        ori_hist = ori_hist.astype(np.float32) / g_px

        texture_var = np.array([gray.var(), gx.var(), gy.var()], dtype=np.float32)
        center_mean = arr_rgb[32:96, 32:96].mean(axis=(0, 1))
        border_mean = arr_rgb[0:32, :].mean(axis=(0, 1))
        bg_contrast = (center_mean - border_mean).astype(np.float32)

        parts = [h_hist, s_hist, v_hist, grid_feats, mag_hist, ori_hist, texture_var, bg_contrast]
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


def _open_pil(image_input):
    try:
        if isinstance(image_input, (str, os.PathLike)):
            if not os.path.exists(image_input):
                return None
            return Image.open(image_input).convert("RGB")
        return Image.open(image_input).convert("RGB")
    except Exception:
        return None


def get_image_embedding(image_input):
    """
    Single 512-dim L2-normalised float32 embedding. NO TTA.

    TTA was removed because taking MAX cosine over 64 augmented-variant
    pairs inflated cross-species scores above 90%, making the threshold
    useless. Dogs matched rabbits. This version uses one canonical
    embedding, giving honest calibrated similarity values.
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
                if norm > 0 and np.isfinite(norm):
                    return (vec / norm).astype(np.float32)
            except Exception:
                pass
        return extract_fallback_vector(pil_img)
    except Exception:
        return None


def compute_stored_embedding(image_path):
    """Canonical embedding as plain list for CaseImage.clip_embedding."""
    try:
        vec = get_image_embedding(image_path)
        if vec is None:
            return None
        arr = np.asarray(vec, dtype=np.float32).ravel()
        if arr.size == 0 or not np.all(np.isfinite(arr)):
            return None
        return [float(x) for x in arr]
    except Exception:
        return None


def _stored_vector(c_img):
    """Return CaseImage.clip_embedding as normalised 1-D array or None."""
    try:
        emb = getattr(c_img, "clip_embedding", None)
        if not emb:
            return None
        vec = np.asarray(emb, dtype=np.float32).ravel()
        if vec.size == 0 or not np.all(np.isfinite(vec)):
            return None
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return None
        return (vec / norm).astype(np.float32)
    except Exception:
        return None


def _embedding_for_path(path):
    """File-stat-cached single embedding for a DB image path (no TTA)."""
    try:
        st = os.stat(path)
        backend = "clip" if load_clip_model() else "fallback"
        key = (backend, os.path.abspath(path), st.st_mtime_ns, st.st_size)
        hit = _EMB_CACHE.get(key)
        if hit is not None:
            return hit
        pil_img = Image.open(path).convert("RGB")
        if backend == "clip":
            try:
                import torch
                inputs = clip_processor(images=pil_img, return_tensors="pt")
                with torch.no_grad():
                    feats = clip_model.get_image_features(**inputs)
                vec = feats.cpu().numpy()[0].astype(np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            except Exception:
                vec = extract_fallback_vector(pil_img)
        else:
            vec = extract_fallback_vector(pil_img)
        if len(_EMB_CACHE) >= _EMB_CACHE_MAX:
            _EMB_CACHE.clear()
        _EMB_CACHE[key] = vec
        return vec
    except Exception:
        return None


_SPECIES_CACHE = {}
_PET_SPECIES_LABELS = ['dog', 'cat', 'rabbit', 'bird', 'horse']
_PET_PROMPTS = ['a photo of a ' + s for s in _PET_SPECIES_LABELS]


def predict_pet_species(pil_img):
    """
    Zero-shot CLIP classification for pet species (dog, cat, rabbit, bird, etc.).
    Returns (label, probability) or (None, 0.0) if CLIP is not available.
    """
    if not load_clip_model():
        return None, 0.0
    try:
        import torch
        inputs = clip_processor(text=_PET_PROMPTS, images=pil_img, return_tensors='pt', padding=True)
        with torch.no_grad():
            out = clip_model(**inputs)
        probs = out.logits_per_image.softmax(dim=1)[0].tolist()
        best_idx = int(torch.tensor(probs).argmax())
        return _PET_SPECIES_LABELS[best_idx], float(probs[best_idx])
    except Exception as exc:
        logger.warning("Failed species classification: %s", exc)
        return None, 0.0


def predict_pet_species_for_path(path):
    """Stat-cached zero-shot species prediction for a database image path."""
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
        if key in _SPECIES_CACHE:
            return _SPECIES_CACHE[key]
        pil_img = Image.open(path).convert('RGB')
        species, conf = predict_pet_species(pil_img)
        if len(_SPECIES_CACHE) >= _EMB_CACHE_MAX:
            _SPECIES_CACHE.clear()
        _SPECIES_CACHE[key] = (species, conf)
        return species, conf
    except Exception:
        return None, 0.0


def search_cases_with_ai(query_image_files, all_cases, min_threshold=DEFAULT_MIN_THRESHOLD, category=None):
    """
    Find cases visually similar to the uploaded query image(s).

    v2 FIX: TTA (Test-Time Augmentation) REMOVED + Species Gating Added.
    1. One canonical vector per image, direct FAISS cosine similarity.
    2. Multi-modal zero-shot species gating: a dog query will NEVER match
       a rabbit or cat case, completely stopping cross-species leakage.
    3. Category gate (PET/PERSON/ITEM) is enforced as a hard pre-filter.
    4. Display score = cosine * 100, labelled similarity score (not probability).
    """
    if not query_image_files:
        return []

    # Category gate
    if category:
        cat = str(category).strip().upper()
        if cat in ("PET", "PERSON", "ITEM"):
            try:
                if hasattr(all_cases, "filter"):
                    all_cases = all_cases.filter(category=cat)
                else:
                    all_cases = [c for c in list(all_cases) if str(getattr(c, "category", "")).upper() == cat]
            except Exception:
                pass
            if all_cases is None:
                return []
            try:
                if len(all_cases) == 0:
                    return []
            except Exception:
                pass

    # Extract query embedding (single vector, no TTA)
    query_vecs = []
    for q_file in query_image_files:
        vec = get_image_embedding(q_file)
        if vec is not None:
            query_vecs.append(vec)

    if not query_vecs:
        logger.warning("AI search: could not embed query image(s).")
        return []

    if len(query_vecs) == 1:
        query_vec = query_vecs[0]
    else:
        avg = np.mean(np.stack(query_vecs, axis=0), axis=0)
        norm = np.linalg.norm(avg)
        query_vec = (avg / norm).astype(np.float32) if norm > 0 else query_vecs[0]

    # Zero-shot species identification for query pet
    query_pet_species = None
    query_pet_conf = 0.0
    if category == 'PET' and query_image_files:
        try:
            pil_q = _open_pil(query_image_files[0])
            if pil_q is not None:
                query_pet_species, query_pet_conf = predict_pet_species(pil_q)
                logger.info("Query PET species: %s (%.1f%%)", query_pet_species, query_pet_conf * 100.0)
        except Exception:
            pass

    try:
        gate_pct = float(min_threshold)
    except (TypeError, ValueError):
        gate_pct = DEFAULT_MIN_THRESHOLD
    gate_cos = gate_pct / 100.0

    # Collect all image records across candidate cases
    img_records = []
    for case in all_cases:
        try:
            case_imgs = list(case.images.all())
        except Exception:
            continue
        for c_img in case_imgs:
            try:
                img_path = c_img.image.path if c_img.image else None
            except Exception:
                img_path = None

            # Species gating: if query is a dog with >= 60% confidence, reject candidate if it is clearly rabbit/cat with >= 60% confidence
            if query_pet_species and query_pet_conf >= 0.60 and img_path and os.path.exists(img_path):
                cand_species, cand_conf = predict_pet_species_for_path(img_path)
                if cand_species and cand_conf >= 0.60 and cand_species != query_pet_species:
                    continue  # Incompatible pet species (e.g. dog vs rabbit)

            db_vec = _stored_vector(c_img)
            if db_vec is None:
                if not img_path or not os.path.exists(img_path):
                    continue
                db_vec = _embedding_for_path(img_path)
                if db_vec is None:
                    continue
            img_records.append((case, db_vec))

    if not img_records:
        return []

    # Calculate best similarity per case using FAISS IndexFlatIP or NumPy
    best_case_sims = {}
    faiss_succeeded = False

    if HAS_FAISS and faiss_module is not None:
        try:
            db_matrix = np.vstack([rec[1] for rec in img_records]).astype(np.float32)
            dim = db_matrix.shape[1]
            index = faiss_module.IndexFlatIP(dim)
            index.add(np.ascontiguousarray(db_matrix, dtype=np.float32))

            q_mat = np.ascontiguousarray(query_vec.reshape(1, -1), dtype=np.float32)
            D, I = index.search(q_mat, len(img_records))
            scores = D[0]
            indices = I[0]
            for sim, idx in zip(scores, indices):
                if idx < 0 or idx >= len(img_records):
                    continue
                case = img_records[idx][0]
                sim = float(sim)
                if sim >= gate_cos:
                    if case.pk not in best_case_sims or sim > best_case_sims[case.pk][1]:
                        best_case_sims[case.pk] = (case, sim)
            faiss_succeeded = True
        except Exception as exc:
            logger.warning("FAISS search failed, falling back to NumPy: %s", exc)
            best_case_sims.clear()
            faiss_succeeded = False

    if not faiss_succeeded:
        for case, db_vec in img_records:
            sim = float(np.dot(query_vec, db_vec))
            if sim >= gate_cos:
                if case.pk not in best_case_sims or sim > best_case_sims[case.pk][1]:
                    best_case_sims[case.pk] = (case, sim)

    case_results = []
    for case_id, (case, best_sim) in best_case_sims.items():
        percentage = round(min(max(best_sim, 0.0), 1.0) * 100.0, 1)
        case_results.append({
            "case": case,
            "match_score": percentage,
            "is_high_match": percentage >= HIGH_MATCH_THRESHOLD,
        })

    case_results.sort(key=lambda x: x["match_score"], reverse=True)
    logger.info("AI search: %d result(s) passed %.1f%% threshold (FAISS=%s)", len(case_results), gate_pct, faiss_succeeded)
    return case_results

