from django.contrib import admin
from .models import (
    Profile, Case, CaseImage, SightingReport, DetectiveRequest,
    CaseAssignment, InvestigationUpdate, DetectiveAchievement,
    Notification, Blog, Feedback
)


class CaseImageInline(admin.TabularInline):
    model = CaseImage
    extra = 1


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'user', 'phone', 'city', 'is_detective', 'detective_status', 'is_banned', 'created_at')
    list_filter = ('is_detective', 'detective_status', 'is_banned')
    search_fields = ('full_name', 'user__username', 'user__email', 'city')
    list_editable = ('is_banned',)


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ('case_number', 'title', 'case_type', 'category', 'status', 'owner', 'assigned_detective', 'location', 'reward', 'created_at')
    list_filter = ('case_type', 'category', 'status')
    search_fields = ('case_number', 'title', 'location', 'owner__username')
    inlines = [CaseImageInline]
    list_editable = ('status',)


@admin.register(CaseImage)
class CaseImageAdmin(admin.ModelAdmin):
    list_display = ('case', 'is_primary', 'image')
    list_filter = ('is_primary',)


@admin.register(SightingReport)
class SightingReportAdmin(admin.ModelAdmin):
    list_display = ('case', 'reported_by', 'reporter_name', 'location', 'created_at')
    search_fields = ('case__case_number', 'location', 'reporter_name')


@admin.register(DetectiveRequest)
class DetectiveRequestAdmin(admin.ModelAdmin):
    list_display = ('case', 'requested_by', 'status', 'created_at')
    list_filter = ('status',)
    list_editable = ('status',)


@admin.register(CaseAssignment)
class CaseAssignmentAdmin(admin.ModelAdmin):
    list_display = ('case', 'detective', 'assigned_by', 'status', 'assigned_at')
    list_filter = ('status',)
    list_editable = ('status',)


@admin.register(InvestigationUpdate)
class InvestigationUpdateAdmin(admin.ModelAdmin):
    list_display = ('title', 'assignment', 'progress', 'created_at')
    search_fields = ('title', 'notes')


@admin.register(DetectiveAchievement)
class DetectiveAchievementAdmin(admin.ModelAdmin):
    list_display = ('detective', 'title', 'case', 'created_at')
    search_fields = ('title', 'detective__username')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_read', 'created_at')
    list_filter = ('is_read',)
    search_fields = ('title', 'user__username')


@admin.register(Blog)
class BlogAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'created_at')
    search_fields = ('title', 'content')


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('sender', 'is_approved', 'created_at')
    list_filter = ('is_approved',)
    list_editable = ('is_approved',)
    search_fields = ('comment', 'sender__username')
