from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from app import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Home
    path('', views.home, name='home'),

    # Authentication
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('api/check-availability/', views.check_availability, name='check_availability'),

    # Cases
    path('cases/', views.case_list, name='case_list'),
    path('case/<int:pk>/', views.case_detail, name='case_detail'),
    path('case/create/', views.case_create, name='case_create'),
    path('case/<int:pk>/edit/', views.case_edit, name='case_edit'),
    path('case/<int:pk>/delete/', views.case_delete, name='case_delete'),
    path('case/<int:pk>/resolve/', views.case_mark_resolved, name='case_mark_resolved'),
    path('ai-vector-search/', views.ai_vector_search_api, name='ai_vector_search_api'),

    # User Dashboard
    path('dashboard/', views.user_dashboard, name='user_dashboard'),
    path('profile/update/', views.profile_update, name='profile_update'),
    path('avatar/upload/', views.avatar_upload, name='avatar_upload'),

    # Detectives
    path('detectives/', views.detective_list, name='detective_list'),
    path('detective/<int:pk>/', views.detective_profile, name='detective_profile'),
    path('detective/dashboard/', views.detective_dashboard, name='detective_dashboard'),
    path('detective/update/<int:assignment_pk>/', views.detective_add_update, name='detective_add_update'),
    path('detective/accept/<int:assignment_pk>/', views.detective_accept_case, name='detective_accept_case'),
    path('detective/request/', views.detective_request_create, name='detective_request_create'),

    # Admin Dashboard
    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/approve-detective/<int:pk>/', views.admin_approve_detective, name='admin_approve_detective'),
    path('admin-panel/assign-detective/', views.admin_assign_detective, name='admin_assign_detective'),
    path('admin-panel/toggle-ban/<int:pk>/', views.admin_toggle_ban, name='admin_toggle_ban'),
    path('admin-panel/blog/', views.admin_manage_blog, name='admin_manage_blog'),
    path('admin-panel/feedback/<int:pk>/', views.admin_manage_feedback, name='admin_manage_feedback'),
    path('admin-panel/case/<int:pk>/delete/', views.admin_delete_case, name='admin_delete_case'),
    path('admin-panel/case/<int:pk>/solve/', views.admin_mark_case_solved, name='admin_mark_case_solved'),

    # Notifications
    path('notifications/', views.notifications_list, name='notifications_list'),
    path('notification/<int:pk>/read/', views.notification_mark_read, name='notification_mark_read'),
    path('notifications/read-all/', views.notifications_mark_all_read, name='notifications_mark_all_read'),

    # Feedback
    path('feedback/create/', views.feedback_create, name='feedback_create'),

    # Sighting Reports
    path('sighting/<int:case_pk>/create/', views.sighting_create, name='sighting_create'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)