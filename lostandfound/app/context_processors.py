from .models import Notification, Profile


def global_context(request):
    ctx = {}
    if request.user.is_authenticated:
        ctx['unread_count'] = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        try:
            ctx['user_profile'] = request.user.profile
        except Profile.DoesNotExist:
            ctx['user_profile'] = None
    return ctx
