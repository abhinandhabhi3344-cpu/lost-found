from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from functools import wraps

from .models import (
    Profile, Case, CaseImage, SightingReport, DetectiveRequest, CaseSolveRequest,
    CaseAssignment, InvestigationUpdate, DetectiveAchievement,
    Notification, Blog, Feedback,
    CASE_TYPE, CATEGORY, CASE_STATUS
)


# ============================================================
# CITIZEN ID-VERIFICATION HELPERS
# ============================================================

def is_verified_for_site(user):
    """Staff + detectives bypass citizen ID check. Citizens need APPROVED."""
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        return False
    if getattr(profile, 'is_banned', False):
        return False
    if getattr(profile, 'is_detective', False):
        return True
    return getattr(profile, 'verification_status', 'PENDING') == 'APPROVED'


def verified_citizen_required(view_func):
    """Block PENDING/REJECTED citizens from authenticated features."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please log in to continue.')
            return redirect('home')
        if request.user.is_staff or request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        try:
            profile = request.user.profile
        except Profile.DoesNotExist:
            messages.error(request, 'Profile missing. Please contact admin.')
            return redirect('home')
        if getattr(profile, 'is_detective', False):
            return view_func(request, *args, **kwargs)
        status = getattr(profile, 'verification_status', 'PENDING')
        if status == 'APPROVED':
            return view_func(request, *args, **kwargs)
        if status == 'REJECTED':
            messages.error(request, 'You are not a verified user by admin. Your account was rejected.')
        else:
            messages.error(request, 'You are not a verified user by admin. Your account is pending verification.')
        return redirect('home')
    return _wrapped


# ============================================================
# AUTHENTICATION VIEWS
# ============================================================

def signup_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '').strip()
        role = request.POST.get('role', 'citizen')

        if not username or not email or not password or not full_name:
            messages.error(request, 'All required fields must be filled.')
            return redirect('home')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already taken.')
            return redirect('home')

        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
            return redirect('home')

        # Citizens MUST upload ID proof. Detectives are exempt.
        id_proof_file = request.FILES.get('id_proof')
        if role != 'detective' and not id_proof_file:
            messages.error(request, 'ID proof is required for citizen registration.')
            return redirect('home')

        name_parts = full_name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        profile = user.profile
        profile.full_name = full_name
        profile.phone = phone
        # Handle avatar upload during signup
        avatar_file = request.FILES.get('avatar')
        if avatar_file:
            profile.avatar = avatar_file

        if role == 'detective':
            profile.is_detective = True
            profile.detective_status = 'PENDING'
            profile.license_number = request.POST.get('license_number', '')
            profile.specialization = request.POST.get('specialization', '')
            profile.experience_years = int(request.POST.get('experience_years', 0) or 0)
            operating_region = request.POST.get('operating_region', '').strip()
            # Store operating region in both address and city for compatibility (admin expects either)
            profile.address = operating_region
            if operating_region:
                profile.city = operating_region
            # Detectives skip citizen ID verification
            profile.verification_status = 'APPROVED'
            profile.verification_reason = ''
        else:
            profile.is_detective = False
            profile.id_proof = id_proof_file
            profile.verification_status = 'PENDING'
            profile.verification_reason = ''

        profile.save()
        login(request, user)

        if role == 'detective':
            messages.success(request, 'Detective application submitted! Awaiting vetting.')
            return redirect('detective_dashboard')
        messages.warning(request, 'Account created! Please wait for admin verification of your ID proof.')
        return redirect('home')

    return redirect('home')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if hasattr(user, 'profile') and user.profile.is_banned:
                messages.error(request, 'Your account has been suspended.')
                return redirect('home')
            login(request, user)

            if user.is_superuser or user.is_staff:
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect('admin_dashboard')
            profile = getattr(user, 'profile', None)
            if profile and profile.is_detective:
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect('detective_dashboard')
            # Citizen ID-verification gate on login (still log them in so nav/modal can render)
            status = getattr(profile, 'verification_status', 'PENDING') if profile else 'PENDING'
            if status == 'APPROVED':
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect('user_dashboard')
            if status == 'REJECTED':
                messages.error(request, 'Your account was rejected by admin. See reason.')
                return redirect('home')
            messages.warning(request, 'Your account is pending verification by admin.')
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password.')
    return redirect('home')


def check_availability(request):
    """AJAX endpoint for live signup validation — checks username/email existence."""
    username = request.GET.get('username', '').strip()
    email = request.GET.get('email', '').strip()
    data = {}
    if username:
        data['username_exists'] = User.objects.filter(username=username).exists()
        data['username'] = username
    if email:
        data['email_exists'] = User.objects.filter(email=email).exists()
        data['email'] = email
    return JsonResponse(data)


def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')


# ============================================================
# HOME PAGE
# ============================================================

def home(request):
    latest_cases = Case.objects.select_related('owner').prefetch_related('images').order_by('-created_at')[:4]
    blogs = Blog.objects.select_related('author').order_by('-created_at')[:3]
    feedbacks = Feedback.objects.filter(is_approved=True).select_related('sender').order_by('-created_at')[:3]
    detectives = Profile.objects.filter(
        is_detective=True, detective_status='APPROVED'
    ).select_related('user').order_by('-experience_years')[:4]

    total_cases = Case.objects.count()
    solved_cases = Case.objects.filter(status__in=['FOUND', 'CLOSED']).count()

    context = {
        'latest_cases': latest_cases,
        'blogs': blogs,
        'feedbacks': feedbacks,
        'detectives': detectives,
        'total_cases': total_cases,
        'solved_cases': solved_cases,
    }
    return render(request, 'home.html', context)


# ============================================================
# CASE CRUD
# ============================================================

def case_list(request):
    cases = Case.objects.select_related('owner', 'assigned_detective').prefetch_related('images').all()

    # Search
    q = request.GET.get('q', '').strip()
    if q:
        cases = cases.filter(
            Q(title__icontains=q) | Q(location__icontains=q) |
            Q(description__icontains=q) | Q(case_number__icontains=q)
        )

    # Filter by type
    case_type = request.GET.get('type', '').upper()
    if case_type in ['LOST', 'FOUND']:
        cases = cases.filter(case_type=case_type)

    # Filter by category
    category = request.GET.get('category', '').upper()
    if category in ['ITEM', 'PET', 'PERSON']:
        cases = cases.filter(category=category)

    # Filter by status
    status = request.GET.get('status', '').upper()
    if status in ['OPEN', 'INVESTIGATING', 'FOUND', 'CLOSED']:
        cases = cases.filter(status=status)

    # Filter by reward
    has_reward = request.GET.get('reward', '')
    if has_reward == '1':
        cases = cases.filter(reward__gt=0)

    # Sort
    sort = request.GET.get('sort', 'latest')
    if sort == 'oldest':
        cases = cases.order_by('created_at')
    elif sort == 'reward':
        cases = cases.order_by('-reward')
    else:
        cases = cases.order_by('-created_at')

    total_count = cases.count()

    # Pagination
    paginator = Paginator(cases, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'cases': page_obj,
        'total_count': total_count,
        'search_query': q,
        'current_type': request.GET.get('type', ''),
        'current_category': request.GET.get('category', ''),
        'current_status': request.GET.get('status', ''),
        'current_sort': sort,
        'has_reward': has_reward,
    }
    return render(request, 'case.html', context)


def case_detail(request, pk):
    case = get_object_or_404(Case.objects.select_related('owner', 'assigned_detective'), pk=pk)
    images = case.images.all()
    sightings = case.sightings.all().order_by('-created_at')

    assignment = CaseAssignment.objects.filter(case=case).select_related('detective').first()
    updates = []
    if assignment:
        updates = assignment.updates.all().order_by('-created_at')

    detective_request = DetectiveRequest.objects.filter(case=case).first()

    context = {
        'case': case,
        'images': images,
        'sightings': sightings,
        'assignment': assignment,
        'updates': updates,
        'detective_request': detective_request,
    }
    return render(request, 'case-details.html', context)


@login_required
@verified_citizen_required
def case_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        case_type = request.POST.get('case_type', 'LOST').upper()
        category = request.POST.get('category', 'PET').upper()
        location = request.POST.get('location', '').strip()
        complaint_number = request.POST.get('complaint_number', '').strip()
        reward = request.POST.get('reward', 0)

        if not title or not location:
            messages.error(request, 'Title and location are required.')
            return redirect('case_list')

        if case_type not in ['LOST', 'FOUND']:
            case_type = 'LOST'

        # Complaint number is required for LOST only (police FIR no. is unique).
        # FOUND items have no complaint number.
        if case_type == 'LOST' and not complaint_number:
            messages.error(request, 'Police complaint registered number is required for lost cases.')
            return redirect('case_list')
        if case_type == 'FOUND':
            complaint_number = ""

        try:
            reward = float(reward) if reward else 0
        except (ValueError, TypeError):
            reward = 0

        case = Case.objects.create(
            owner=request.user,
            title=title,
            description=description,
            case_type=case_type if case_type in ['LOST', 'FOUND'] else 'LOST',
            category=category if category in ['ITEM', 'PET', 'PERSON'] else 'PET',
            location=location,
            complaint_number=complaint_number,
            reward=reward,
        )

        # Handle image upload
        image = request.FILES.get('image')
        if image:
            CaseImage.objects.create(case=case, image=image, is_primary=True)

        Notification.objects.create(
            user=request.user,
            case=case,
            title='Case Created',
            message=f'Your case "{case.title}" ({case.case_number}) has been created successfully.'
        )

        messages.success(request, f'Case {case.case_number} created successfully!')
        return redirect('case_detail', pk=case.pk)

    return redirect('case_list')


@login_required
@verified_citizen_required
def case_edit(request, pk):
    # Only the user who posted the case can edit it
    case = get_object_or_404(Case, pk=pk, owner=request.user)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if title:
            case.title = title
        description = request.POST.get('description', '').strip()
        if description:
            case.description = description
        location = request.POST.get('location', '').strip()
        if location:
            case.location = location
        # Complaint number applies to LOST only; FOUND never stores one.
        if case.case_type == 'LOST':
            complaint_number = request.POST.get('complaint_number', '').strip()
            if complaint_number:
                case.complaint_number = complaint_number
        else:
            case.complaint_number = ""
        category = request.POST.get('category', '').strip().upper()
        if category in ['ITEM', 'PET', 'PERSON']:
            case.category = category
        reward = request.POST.get('reward', case.reward)
        try:
            case.reward = float(reward) if reward not in (None, '') else 0
        except (ValueError, TypeError):
            pass

        case.save()

        image = request.FILES.get('image')
        if image:
            case.images.filter(is_primary=True).update(is_primary=False)
            CaseImage.objects.create(case=case, image=image, is_primary=True)

        messages.success(request, 'Case updated successfully!')
        return redirect('user_dashboard')

    return redirect('user_dashboard')


@login_required
@verified_citizen_required
@require_POST
def case_delete(request, pk):
    case = get_object_or_404(Case, pk=pk)
    if case.owner == request.user or request.user.is_staff:
        case_number = case.case_number
        case.delete()
        messages.success(request, f'Case {case_number} has been deleted.')
        if request.user.is_staff:
            return redirect('admin_dashboard')
        return redirect('user_dashboard')
    messages.error(request, 'You do not have permission to delete this case.')
    return redirect('case_detail', pk=pk)


@login_required
@verified_citizen_required
@require_POST
def case_mark_resolved(request, pk):
    case = get_object_or_404(Case, pk=pk)
    # Staff can close directly (they ARE the verifier)
    if request.user.is_staff:
        if case.owner != request.user and not request.user.is_staff:
            messages.error(request, 'Permission denied.')
            return redirect('case_detail', pk=pk)
        case.status = 'CLOSED'
        case.save()
        CaseSolveRequest.objects.filter(case=case, status='PENDING').update(status='APPROVED', reviewed_at=timezone.now())
        Notification.objects.create(
            user=case.owner,
            case=case,
            title='Case Resolved',
            message=f'Your case "{case.title}" has been marked as solved and closed.'
        )
        messages.success(request, 'Case marked as solved!')
        return redirect('case_detail', pk=pk)
    # Owner flow: send to admin for verification, do NOT close yet
    if case.owner != request.user:
        messages.error(request, 'Permission denied.')
        return redirect('case_detail', pk=pk)
    if case.status in ['CLOSED']:
        messages.warning(request, 'Case is already closed.')
        return redirect('case_detail', pk=pk)
    if CaseSolveRequest.objects.filter(case=case, status='PENDING').exists():
        messages.warning(request, 'A solve verification request is already pending with admin.')
        return redirect('case_detail', pk=pk)
    message_text = request.POST.get('message', '').strip()
    CaseSolveRequest.objects.create(
        case=case,
        requested_by=request.user,
        previous_status=case.status,
        message=message_text,
    )
    for admin_user in User.objects.filter(is_staff=True):
        Notification.objects.create(
            user=admin_user,
            case=case,
            title='Solve Verification Request',
            message=f'User {request.user.profile.full_name} requested to mark case {case.case_number} as solved. Please verify.'
        )
    messages.success(request, 'Solve request sent to admin for verification. Case stays open until approved.')
    # stay on dashboard when coming from dashboard, else detail
    referer = request.META.get('HTTP_REFERER', '')
    if 'dashboard' in referer:
        return redirect('user_dashboard')
    return redirect('case_detail', pk=pk)


# ============================================================
# USER DASHBOARD
# ============================================================

@login_required
@verified_citizen_required
def user_dashboard(request):
    user = request.user
    my_cases = Case.objects.filter(owner=user).prefetch_related('images').order_by('-created_at')
    lost_cases = my_cases.filter(case_type='LOST')
    found_cases = my_cases.filter(case_type='FOUND')

    active_cases = my_cases.filter(status__in=['OPEN', 'INVESTIGATING'])
    solved_cases = my_cases.filter(status__in=['FOUND', 'CLOSED'])

    detective_requests = DetectiveRequest.objects.filter(
        requested_by=user
    ).select_related('case', 'requested_detective', 'requested_detective__profile').order_by('-created_at')

    assignments = CaseAssignment.objects.filter(
        case__owner=user
    ).select_related('case', 'detective', 'detective__profile').prefetch_related('updates')

    notifications = Notification.objects.filter(user=user).order_by('-created_at')[:10]

    # IDs of cases that already have a detective assigned (prevent multiple detectives per case)
    # REJECTED assignments don't block — case becomes requestable again
    assigned_case_ids = set(CaseAssignment.objects.filter(case__owner=user).exclude(status='REJECTED').values_list('case_id', flat=True))
    assigned_via_field = set(Case.objects.filter(owner=user, assigned_detective__isnull=False).values_list('id', flat=True))
    all_assigned_case_ids = assigned_case_ids.union(assigned_via_field)
    pending_request_case_ids = set(DetectiveRequest.objects.filter(requested_by=user, status='PENDING').values_list('case_id', flat=True))

    # Cases eligible for new detective request (not yet assigned, and still open/investigating)
    unassigned_cases = my_cases.filter(
        case_type='LOST', status__in=['OPEN', 'INVESTIGATING']
    ).exclude(id__in=all_assigned_case_ids).exclude(id__in=pending_request_case_ids)

    solve_requests = CaseSolveRequest.objects.filter(requested_by=user).select_related('case').order_by('-created_at')
    pending_solve_case_ids = set(solve_requests.filter(status='PENDING').values_list('case_id', flat=True))

    context = {
        'lost_cases': lost_cases,
        'found_cases': found_cases,
        'active_count': active_cases.count(),
        'solved_count': solved_cases.count(),
        'lost_count': lost_cases.count(),
        'found_count': found_cases.count(),
        'detective_requests': detective_requests,
        'assignments': assignments,
        'notifications': notifications,
        'detective_request_count': detective_requests.count(),
        'assigned_case_ids': all_assigned_case_ids,
        'pending_request_case_ids': pending_request_case_ids,
        'unassigned_cases': unassigned_cases,
        'available_detectives': Profile.objects.filter(is_detective=True, detective_status='APPROVED').select_related('user').order_by('full_name'),
        'solve_requests': solve_requests,
        'pending_solve_case_ids': pending_solve_case_ids,
    }
    return render(request, 'user-dash.html', context)


@login_required
@verified_citizen_required
def profile_update(request):
    if request.method == 'POST':
        user = request.user
        profile = user.profile

        new_username = request.POST.get('username', '').strip()
        if new_username and new_username != user.username:
            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                messages.error(request, 'Username already taken.')
                return redirect('user_dashboard')
            user.username = new_username

        full_name = request.POST.get('full_name', '').strip()
        if full_name:
            profile.full_name = full_name
            parts = full_name.split(' ', 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ''

        email = request.POST.get('email', '').strip()
        if email:
            user.email = email

        phone = request.POST.get('phone', '').strip()
        profile.phone = phone

        address = request.POST.get('address', '').strip()
        if address:
            profile.address = address

        city = request.POST.get('city', '').strip()
        if city:
            profile.city = city

        # Keep city/address in sync for detectives (admin shows city|default:address)
        if profile.is_detective:
            if profile.city and not profile.address:
                profile.address = profile.city
            if profile.address and not profile.city:
                profile.city = profile.address

        user.save()
        profile.save()
        messages.success(request, 'Profile updated successfully!')
    return redirect('user_dashboard')


@login_required
@verified_citizen_required
def avatar_upload(request):
    if request.method == 'POST' and request.FILES.get('avatar'):
        profile = request.user.profile
        if profile.avatar:
            profile.avatar.delete(save=False)
        profile.avatar = request.FILES['avatar']
        profile.save()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok', 'url': profile.avatar.url})
        messages.success(request, 'Avatar updated!')
    return redirect('user_dashboard')


# ============================================================
# DETECTIVE VIEWS
# ============================================================

def detective_list(request):
    detectives = Profile.objects.filter(
        is_detective=True, detective_status='APPROVED'
    ).select_related('user').order_by('-experience_years')

    q = request.GET.get('q', '').strip()
    if q:
        detectives = detectives.filter(
            Q(full_name__icontains=q) | Q(specialization__icontains=q) |
            Q(city__icontains=q) | Q(address__icontains=q)
        )

    context = {
        'detectives': detectives,
        'search_query': q,
    }
    return render(request, 'dictatives.html', context)


def detective_profile(request, pk):
    profile = get_object_or_404(
        Profile.objects.select_related('user'),
        pk=pk, is_detective=True
    )
    achievements = DetectiveAchievement.objects.filter(detective=profile.user).order_by('-created_at')

    assigned_cases = CaseAssignment.objects.filter(
        detective=profile.user
    ).select_related('case').prefetch_related('updates')

    solved_count = assigned_cases.filter(status='COMPLETED').count()
    active_count = assigned_cases.filter(status__in=['PENDING', 'ACCEPTED']).count()

    context = {
        'detective': profile,
        'achievements': achievements,
        'assigned_cases': assigned_cases,
        'solved_count': solved_count,
        'active_count': active_count,
    }
    return render(request, 'dictative-profile-view.html', context)


@login_required
@verified_citizen_required
def detective_dashboard(request):
    user = request.user
    if not hasattr(user, 'profile') or not user.profile.is_detective:
        messages.error(request, 'Access denied. Detective accounts only.')
        return redirect('home')

    assignments = CaseAssignment.objects.filter(
        detective=user
    ).select_related('case', 'case__owner').prefetch_related('updates', 'case__images').order_by('-assigned_at')

    active_assignments = assignments.filter(status__in=['PENDING', 'ACCEPTED'])
    completed_assignments = assignments.filter(status='COMPLETED')
    rejected_assignments = assignments.filter(status='REJECTED')

    achievements = DetectiveAchievement.objects.filter(detective=user).order_by('-created_at')
    notifications = Notification.objects.filter(user=user).order_by('-created_at')[:10]

    # All investigation updates across assigned cases, newest first — so detective can see just-updated data
    all_updates = InvestigationUpdate.objects.filter(assignment__detective=user).select_related('assignment', 'assignment__case').order_by('-created_at')[:20]

    context = {
        'assignments': assignments,
        'active_assignments': active_assignments,
        'completed_assignments': completed_assignments,
        'rejected_assignments': rejected_assignments,
        'achievements': achievements,
        'notifications': notifications,
        'active_count': active_assignments.count(),
        'completed_count': completed_assignments.count(),
        'rejected_count': rejected_assignments.count(),
        'total_count': assignments.exclude(status='REJECTED').count(),
        'all_updates': all_updates,
    }
    return render(request, 'dictative-dash.html', context)


@login_required
@verified_citizen_required
@require_POST
def detective_add_update(request, assignment_pk):
    assignment = get_object_or_404(CaseAssignment, pk=assignment_pk, detective=request.user)

    title = request.POST.get('title', '').strip()
    notes = request.POST.get('notes', '').strip()
    progress = request.POST.get('progress', 0)

    try:
        progress = int(progress)
    except (ValueError, TypeError):
        progress = 0

    if not title or not notes:
        messages.error(request, 'Title and notes are required.')
        return redirect('detective_dashboard')

    update = InvestigationUpdate.objects.create(
        assignment=assignment,
        title=title,
        notes=notes,
        progress=min(progress, 100),
        evidence_photo=request.FILES.get('evidence_photo'),
    )

    # Notify case owner
    Notification.objects.create(
        user=assignment.case.owner,
        case=assignment.case,
        title='Investigation Update',
        message=f'Detective {request.user.profile.full_name} posted an update: "{title}"'
    )

    if progress >= 100:
        assignment.status = 'COMPLETED'
        assignment.save()
        assignment.case.status = 'FOUND'
        assignment.case.save()

    messages.success(request, 'Investigation update posted!')
    return redirect('detective_dashboard')


@login_required
@verified_citizen_required
@require_POST
def detective_accept_case(request, assignment_pk):
    assignment = get_object_or_404(CaseAssignment, pk=assignment_pk, detective=request.user)
    if assignment.status not in ['PENDING']:
        messages.error(request, 'This assignment can no longer be accepted.')
        return redirect('detective_dashboard')
    assignment.status = 'ACCEPTED'
    assignment.save()
    assignment.case.status = 'INVESTIGATING'
    assignment.case.save()

    Notification.objects.create(
        user=assignment.case.owner,
        case=assignment.case,
        title='Detective Accepted',
        message=f'Detective {request.user.profile.full_name} accepted your case assignment.'
    )
    messages.success(request, 'Case accepted!')
    return redirect('detective_dashboard')


@login_required
@verified_citizen_required
@require_POST
def detective_reject_case(request, assignment_pk):
    assignment = get_object_or_404(CaseAssignment, pk=assignment_pk, detective=request.user)
    if assignment.status in ['COMPLETED', 'REJECTED']:
        messages.error(request, 'This assignment can no longer be rejected.')
        return redirect('detective_dashboard')

    reason = request.POST.get('reject_reason', '').strip()
    if not reason:
        messages.error(request, 'Please provide a reason for rejecting this case.')
        return redirect('detective_dashboard')

    assignment.status = 'REJECTED'
    assignment.reject_reason = reason
    assignment.rejected_at = timezone.now()
    assignment.save()

    # Free the case so the owner can request another detective
    case = assignment.case
    case.assigned_detective = None
    # Revert INVESTIGATING back to OPEN if no other active assignment exists
    has_other_active = CaseAssignment.objects.filter(case=case).exclude(pk=assignment.pk).exclude(status='REJECTED').exists()
    if not has_other_active and case.status == 'INVESTIGATING':
        case.status = 'OPEN'
    case.save()

    detective_name = request.user.profile.full_name if hasattr(request.user, 'profile') else request.user.username
    Notification.objects.create(
        user=case.owner,
        case=case,
        title='Detective Declined Case',
        message=f'Detective {detective_name} declined your case "{case.title}" ({case.case_number}). Reason: {reason}'
    )
    messages.success(request, 'Case rejected. The case owner has been notified with your reason.')
    return redirect('detective_dashboard')


# ============================================================
# DETECTIVE REQUEST (USER REQUESTS DETECTIVE FOR CASE)
# ============================================================

@login_required
@verified_citizen_required
@require_POST
def detective_request_create(request):
    case_pk = request.POST.get('case_id')
    detective_pk = request.POST.get('detective_id')
    message_text = request.POST.get('message', '').strip()

    case = get_object_or_404(Case, pk=case_pk, owner=request.user)

    # Block requests for closed/resolved cases — no investigation needed
    if case.status in ['CLOSED', 'FOUND']:
        messages.error(request, 'Cannot request a detective for a closed or resolved case.')
        return redirect('user_dashboard')

    # Prevent multiple detectives per case: if already assigned, block request
    # REJECTED assignments don't count — owner may re-request
    if CaseAssignment.objects.filter(case=case).exclude(status='REJECTED').exists() or case.assigned_detective is not None:
        messages.error(request, 'A detective is already assigned to this case. Cannot request another.')
        return redirect('user_dashboard')

    if DetectiveRequest.objects.filter(case=case, status='PENDING').exists():
        messages.warning(request, 'A detective request is already pending for this case.')
        return redirect('user_dashboard')

    # User must select a detective — validate approved detective user id
    if not detective_pk:
        messages.error(request, 'Please select a detective for your case.')
        return redirect('user_dashboard')
    try:
        selected_detective_user = User.objects.get(pk=detective_pk, profile__is_detective=True, profile__detective_status='APPROVED')
    except User.DoesNotExist:
        messages.error(request, 'Selected detective is invalid or not approved.')
        return redirect('user_dashboard')

    DetectiveRequest.objects.create(
        case=case,
        requested_by=request.user,
        requested_detective=selected_detective_user,
        message=message_text,
    )

    # Notify admins
    for admin_user in User.objects.filter(is_staff=True):
        Notification.objects.create(
            user=admin_user,
            case=case,
            title='New Detective Request',
            message=f'User {request.user.profile.full_name} requested detective {selected_detective_user.profile.full_name} for case {case.case_number}.'
        )

    messages.success(request, f'Request for detective {selected_detective_user.profile.full_name} submitted! Awaiting admin approval.')
    return redirect('user_dashboard')


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@login_required
def admin_dashboard(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    total_users = User.objects.filter(is_staff=False).count()
    total_detectives = Profile.objects.filter(is_detective=True, detective_status='APPROVED').count()
    total_lost = Case.objects.filter(case_type='LOST').count()
    total_found = Case.objects.filter(case_type='FOUND').count()
    solved_cases = Case.objects.filter(status__in=['FOUND', 'CLOSED']).count()
    active_cases = Case.objects.filter(status__in=['OPEN', 'INVESTIGATING']).count()
    total_blogs = Blog.objects.count()
    pending_detectives = Profile.objects.filter(is_detective=True, detective_status='PENDING').count()
    pending_user_verifications = Profile.objects.filter(
        is_detective=False, verification_status='PENDING', user__is_staff=False
    ).select_related('user').order_by('-created_at')
    pending_user_count = pending_user_verifications.count()

    users = Profile.objects.select_related('user').filter(user__is_staff=False).order_by('-created_at')
    pending_detective_apps = Profile.objects.filter(
        is_detective=True, detective_status='PENDING'
    ).select_related('user')

    cases = Case.objects.select_related('owner', 'assigned_detective').order_by('-created_at')[:20]

    detective_requests = DetectiveRequest.objects.filter(
        status='PENDING'
    ).select_related('case', 'requested_by', 'requested_by__profile', 'requested_detective', 'requested_detective__profile')

    pending_solve_requests = CaseSolveRequest.objects.filter(
        status='PENDING'
    ).select_related('case', 'requested_by', 'requested_by__profile').order_by('-created_at')

    approved_detectives = Profile.objects.filter(
        is_detective=True, detective_status='APPROVED'
    ).select_related('user')

    blogs = Blog.objects.select_related('author').order_by('-created_at')
    feedbacks = Feedback.objects.select_related('sender').order_by('-created_at')
    pending_feedbacks = feedbacks.filter(is_approved=False).count()

    context = {
        'total_users': total_users,
        'total_detectives': total_detectives,
        'total_lost': total_lost,
        'total_found': total_found,
        'solved_cases': solved_cases,
        'active_cases': active_cases,
        'total_blogs': total_blogs,
        'pending_detectives': pending_detectives,
        'pending_user_verifications': pending_user_verifications,
        'pending_user_count': pending_user_count,
        'users': users,
        'pending_detective_apps': pending_detective_apps,
        'cases': cases,
        'detective_requests': detective_requests,
        'pending_solve_requests': pending_solve_requests,
        'approved_detectives': approved_detectives,
        'blogs': blogs,
        'feedbacks': feedbacks,
        'pending_feedbacks': pending_feedbacks,
    }
    return render(request, 'admin-dash.html', context)


@login_required
@require_POST
def admin_approve_detective(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    profile = get_object_or_404(Profile, pk=pk, is_detective=True)
    action = request.POST.get('action', 'approve')

    if action == 'approve':
        profile.detective_status = 'APPROVED'
        profile.save()
        Notification.objects.create(
            user=profile.user,
            title='Application Approved',
            message='Your detective application has been approved! You can now receive case assignments.'
        )
        messages.success(request, f'Detective {profile.full_name} approved.')
    elif action == 'reject':
        profile.detective_status = 'REJECTED'
        profile.save()
        Notification.objects.create(
            user=profile.user,
            title='Application Rejected',
            message='Your detective application has been rejected.'
        )
        messages.success(request, f'Detective {profile.full_name} rejected.')

    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_verify_user(request, pk):
    """Admin approves or rejects a citizen after checking ID proof + details."""
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    profile = get_object_or_404(Profile, pk=pk, is_detective=False)
    action = request.POST.get('action', 'approve')

    if action == 'approve':
        profile.verification_status = 'APPROVED'
        profile.verification_reason = ''
        profile.verified_at = timezone.now()
        profile.save()
        Notification.objects.create(
            user=profile.user,
            title='Account Verified',
            message='Your account has been verified by admin! You can now use all platform features.'
        )
        messages.success(request, f'User {profile.full_name} verified.')
    elif action == 'reject':
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'Please provide a reason for rejecting this user.')
            return redirect('admin_dashboard')
        profile.verification_status = 'REJECTED'
        profile.verification_reason = reason
        profile.verified_at = timezone.now()
        profile.save()
        Notification.objects.create(
            user=profile.user,
            case=None,
            title='Account Rejected by Admin',
            message=f'Your account verification was rejected by admin. Reason: {reason}'
        )
        messages.success(request, f'User {profile.full_name} rejected.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def delete_own_account(request):
    """Rejected (or any non-staff) user deletes their own account + cleanup."""
    user = request.user
    if user.is_staff or user.is_superuser:
        messages.error(request, 'Admin accounts cannot be deleted this way.')
        return redirect('home')
    username = user.username
    try:
        profile = user.profile
        # Cleanup uploaded files
        try:
            if profile.avatar:
                profile.avatar.delete(save=False)
        except Exception:
            pass
        try:
            if profile.id_proof:
                profile.id_proof.delete(save=False)
        except Exception:
            pass
    except Profile.DoesNotExist:
        pass
    logout(request)
    User.objects.filter(pk=user.pk).delete()
    messages.success(request, f'Account {username} has been deleted.')
    return redirect('home')


@login_required
@require_POST
def admin_assign_detective(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    case_pk = request.POST.get('case_id')
    detective_pk = request.POST.get('detective_id')
    request_pk = request.POST.get('request_id')

    case = get_object_or_404(Case, pk=case_pk)

    # Resolve detective: prefer user-selected detective from the request on approval
    detective_request = None
    if request_pk:
        detective_request = DetectiveRequest.objects.select_related('requested_detective').filter(pk=request_pk).first()
        if detective_request and detective_request.status != 'PENDING':
            messages.error(request, 'This detective request has already been reviewed.')
            return redirect('admin_dashboard')
        # If admin did not pick a different detective, use the user-selected one
        if not detective_pk and detective_request and detective_request.requested_detective_id:
            detective_user = detective_request.requested_detective
            detective_profile = detective_user.profile
            if not (detective_profile.is_detective and detective_profile.detective_status == 'APPROVED'):
                messages.error(request, 'User-selected detective is no longer approved.')
                return redirect('admin_dashboard')
        else:
            detective_profile = get_object_or_404(Profile, pk=detective_pk, is_detective=True, detective_status='APPROVED')
            detective_user = detective_profile.user
    else:
        detective_profile = get_object_or_404(Profile, pk=detective_pk, is_detective=True, detective_status='APPROVED')
        detective_user = detective_profile.user

    # Block assignment to closed/resolved cases
    if case.status in ['CLOSED', 'FOUND']:
        messages.error(request, f'Case {case.case_number} is closed/resolved. Cannot assign a detective.')
        return redirect('admin_dashboard')

    # Prevent multiple detectives for a single case (REJECTED doesn't block re-assign)
    if CaseAssignment.objects.filter(case=case).exclude(status='REJECTED').exists() or case.assigned_detective is not None:
        messages.error(request, f'Case {case.case_number} already has a detective assigned. Multiple detectives per case is not allowed.')
        return redirect('admin_dashboard')

    # Create assignment
    assignment = CaseAssignment.objects.create(
        case=case,
        detective=detective_profile.user,
        assigned_by=request.user,
    )

    # Update case
    case.assigned_detective = detective_profile.user
    case.status = 'INVESTIGATING'
    case.save()

    # Update detective request if provided
    if request_pk and detective_request:
        detective_request.status = 'APPROVED'
        detective_request.reviewed_at = timezone.now()
        detective_request.save()

    # Notify detective
    Notification.objects.create(
        user=detective_user,
        case=case,
        title='New Case Assignment',
        message=f'You have been assigned to case {case.case_number}: {case.title}'
    )

    # Notify case owner
    Notification.objects.create(
        user=case.owner,
        case=case,
        title='Detective Assigned',
        message=f'Detective {detective_profile.full_name} has been assigned to your case.'
    )

    messages.success(request, f'Detective {detective_profile.full_name} assigned to case {case.case_number}.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_reject_detective_request(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    request_pk = request.POST.get('request_id')
    reason = request.POST.get('admin_reason', '').strip()
    if not reason:
        messages.error(request, 'Please provide a reason for rejecting this request.')
        return redirect('admin_dashboard')

    det_request = get_object_or_404(DetectiveRequest, pk=request_pk, status='PENDING')
    det_request.status = 'DECLINED'
    det_request.admin_reason = reason
    det_request.reviewed_at = timezone.now()
    det_request.save()

    Notification.objects.create(
        user=det_request.requested_by,
        case=det_request.case,
        title='Detective Request Rejected by Admin',
        message=f'Admin rejected your request for detective {det_request.requested_detective.profile.full_name if det_request.requested_detective and hasattr(det_request.requested_detective, "profile") else "selected detective"} on case "{det_request.case.title}" ({det_request.case.case_number}). Reason: {reason}'
    )
    messages.success(request, 'Detective request rejected. The user has been notified with your reason.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_toggle_ban(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    profile = get_object_or_404(Profile, pk=pk)
    profile.is_banned = not profile.is_banned
    profile.save()

    action = 'banned' if profile.is_banned else 'unbanned'
    messages.success(request, f'User {profile.full_name} has been {action}.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_manage_blog(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    action = request.POST.get('action', 'create')

    if action == 'create':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        image = request.FILES.get('image')

        if not title or not content:
            messages.error(request, 'Blog title and content are required.')
            return redirect('admin_dashboard')

        Blog.objects.create(
            title=title,
            content=content,
            image=image,
            author=request.user,
        )
        messages.success(request, 'Blog article published!')

    elif action == 'delete':
        blog_pk = request.POST.get('blog_id')
        blog = get_object_or_404(Blog, pk=blog_pk)
        blog.delete()
        messages.success(request, 'Blog article deleted.')

    elif action == 'edit':
        blog_pk = request.POST.get('blog_id')
        blog = get_object_or_404(Blog, pk=blog_pk)
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        image = request.FILES.get('image')
        if not title or not content:
            messages.error(request, 'Blog title and content are required.')
            return redirect('admin_dashboard')
        blog.title = title
        blog.content = content
        if image:
            if blog.image:
                blog.image.delete(save=False)
            blog.image = image
        blog.save()
        messages.success(request, 'Blog article updated!')

    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_manage_feedback(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    feedback = get_object_or_404(Feedback, pk=pk)
    action = request.POST.get('action', 'approve')

    if action == 'approve':
        feedback.is_approved = True
        feedback.save()
        messages.success(request, 'Feedback approved and published.')
    elif action == 'reply':
        reply = request.POST.get('reply', '').strip()
        feedback.admin_reply = reply
        feedback.save()
        messages.success(request, 'Reply saved.')
    elif action == 'delete':
        feedback.delete()
        messages.success(request, 'Feedback deleted.')

    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_delete_case(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    case = get_object_or_404(Case, pk=pk)
    case.delete()
    messages.success(request, 'Case deleted by admin.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_mark_case_solved(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')

    case = get_object_or_404(Case, pk=pk)
    case.status = 'CLOSED'
    case.save()
    CaseSolveRequest.objects.filter(case=case, status='PENDING').update(status='APPROVED', reviewed_at=timezone.now())
    Notification.objects.create(
        user=case.owner,
        case=case,
        title='Case Solved',
        message=f'Admin verified and closed your case "{case.title}" ({case.case_number}) as solved.'
    )
    messages.success(request, f'Case {case.case_number} marked as solved.')
    return redirect('admin_dashboard')


@login_required
@require_POST
def admin_review_solve_request(request):
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('home')
    request_pk = request.POST.get('request_id')
    action = request.POST.get('action', 'approve')
    solve_req = get_object_or_404(CaseSolveRequest, pk=request_pk, status='PENDING')
    case = solve_req.case
    if action == 'approve':
        solve_req.status = 'APPROVED'
        solve_req.reviewed_at = timezone.now()
        solve_req.save()
        case.status = 'CLOSED'
        case.save()
        Notification.objects.create(
            user=case.owner,
            case=case,
            title='Solve Request Approved',
            message=f'Admin verified your solve request for "{case.title}" ({case.case_number}). Case is now CLOSED as solved.'
        )
        messages.success(request, f'Solve request approved. Case {case.case_number} CLOSED.')
    else:
        reason = request.POST.get('admin_reason', '').strip()
        if not reason:
            messages.error(request, 'Please provide a reason for rejecting the solve request.')
            return redirect('admin_dashboard')
        solve_req.status = 'DECLINED'
        solve_req.admin_reason = reason
        solve_req.reviewed_at = timezone.now()
        solve_req.save()
        # Restore/keep older open status — case was never closed
        if case.status == 'CLOSED':
            case.status = solve_req.previous_status if solve_req.previous_status in ['OPEN', 'INVESTIGATING', 'FOUND'] else 'OPEN'
            case.save()
        Notification.objects.create(
            user=case.owner,
            case=case,
            title='Solve Request Rejected by Admin',
            message=f'Admin rejected your solve request for "{case.title}" ({case.case_number}). Reason: {reason} Case remains {case.get_status_display()} (open).'
        )
        messages.success(request, 'Solve request rejected. Owner notified with reason; case stays open.')
    return redirect('admin_dashboard')


# ============================================================
# NOTIFICATIONS
# ============================================================

@login_required
@verified_citizen_required
def notifications_list(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:20]
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        data = [{
            'id': n.pk,
            'title': n.title,
            'message': n.message,
            'is_read': n.is_read,
            'created_at': n.created_at.strftime('%b %d, %Y %I:%M %p'),
        } for n in notifications]
        return JsonResponse({'notifications': data})
    return redirect('user_dashboard')


@login_required
@verified_citizen_required
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.is_read = True
    notification.save()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    return redirect('user_dashboard')


@login_required
@verified_citizen_required
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    messages.success(request, 'All notifications marked as read.')
    return redirect('user_dashboard')


# ============================================================
# FEEDBACK
# ============================================================

@login_required
@verified_citizen_required
@require_POST
def feedback_create(request):
    comment = request.POST.get('comment', '').strip()
    if not comment:
        messages.error(request, 'Feedback comment is required.')
        return redirect('home')

    Feedback.objects.create(
        sender=request.user,
        comment=comment,
    )
    messages.success(request, 'Thank you! Your feedback has been submitted for admin review.')
    return redirect('home')


# ============================================================
# SIGHTING REPORTS
# ============================================================

@login_required
@verified_citizen_required
@require_POST
def sighting_create(request, case_pk):
    case = get_object_or_404(Case, pk=case_pk)

    # Prevent case owner from reporting sighting on own case (meant for other users)
    if case.owner == request.user:
        messages.error(request, 'You cannot report a sighting on your own case. This feature is for other users.')
        return redirect('case_detail', pk=case_pk)

    location = request.POST.get('location', '').strip()
    description = request.POST.get('description', '').strip()
    reporter_name = request.POST.get('reporter_name', '').strip()
    reporter_contact = request.POST.get('reporter_contact', '').strip()

    if not location or not description:
        messages.error(request, 'Location and description are required.')
        return redirect('case_detail', pk=case_pk)

    SightingReport.objects.create(
        case=case,
        reported_by=request.user,
        reporter_name=reporter_name or request.user.profile.full_name,
        reporter_contact=reporter_contact,
        location=location,
        description=description,
        photo=request.FILES.get('photo'),
    )

    # Notify case owner
    Notification.objects.create(
        user=case.owner,
        case=case,
        title='New Sighting Report',
        message=f'A new sighting has been reported for your case "{case.title}" at {location}.'
    )

    messages.success(request, 'Sighting report submitted!')
    return redirect('case_detail', pk=case_pk)


# ============================================================
# REAL AI VECTOR SEARCH API (CLIP & FAISS)
# ============================================================

from django.views.decorators.csrf import csrf_exempt
from .ai_engine import search_cases_with_ai

@csrf_exempt
def ai_vector_search_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required.'}, status=405)

    files = request.FILES.getlist('images') or request.FILES.getlist('image')
    if not files and 'file' in request.FILES:
        files = [request.FILES['file']]

    if not files:
        return JsonResponse({'success': False, 'error': 'No query image file uploaded.'}, status=400)

    try:
        cases = Case.objects.select_related('owner').prefetch_related('images').all()
        results = search_cases_with_ai(files, cases)

        data = []
        for r in results:
            data.append({
                'case_id': r['case'].pk,
                'case_number': r['case'].case_number,
                'title': r['case'].title,
                'match_score': r['match_score'],
                'is_high_match': r['is_high_match'],
            })

        return JsonResponse({
            'success': True,
            'total_analyzed': len(results),
            'results': data
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

