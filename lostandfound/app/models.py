
# Simplified models.py for Lost & Found Project
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid

CASE_TYPE=[('LOST','Lost'),('FOUND','Found')]
CATEGORY=[('ITEM','Item'),('PET','Pet'),('PERSON','Person')]
CASE_STATUS=[('OPEN','Open'),('INVESTIGATING','Investigating'),('FOUND','Found'),('CLOSED','Closed')]
DETECTIVE_STATUS=[('PENDING','Pending'),('APPROVED','Approved'),('REJECTED','Rejected')]
USER_VERIFICATION=[('PENDING','Pending'),('APPROVED','Approved'),('REJECTED','Rejected')]
REQUEST_STATUS=[('PENDING','Pending'),('APPROVED','Approved'),('DECLINED','Declined')]
ASSIGNMENT_STATUS=[('PENDING','Pending'),('ACCEPTED','Accepted'),('COMPLETED','Completed'),('REJECTED','Rejected')]

def case_no():
    return f"LF-{timezone.now().year}-{uuid.uuid4().hex[:6].upper()}"

class Profile(models.Model):
    user=models.OneToOneField(User,on_delete=models.CASCADE)
    full_name=models.CharField(max_length=150)
    phone=models.CharField(max_length=20,blank=True)
    address=models.CharField(max_length=255,blank=True)
    city=models.CharField(max_length=100,blank=True)
    avatar=models.ImageField(upload_to="avatars/",max_length=200,blank=True,null=True)
    is_detective=models.BooleanField(default=False)
    is_banned=models.BooleanField(default=False)
    detective_status=models.CharField(max_length=8,choices=DETECTIVE_STATUS,default="PENDING",db_index=True)
    license_number=models.CharField(max_length=100,blank=True)
    specialization=models.CharField(max_length=200,blank=True)
    experience_years=models.PositiveIntegerField(default=0)
    # Citizen ID verification (required for citizens, not for detectives)
    id_proof=models.FileField(upload_to="id_proofs/",max_length=200,blank=True,null=True)
    verification_status=models.CharField(max_length=8,choices=USER_VERIFICATION,default="PENDING",db_index=True)
    verification_reason=models.TextField(blank=True,default="")
    verified_at=models.DateTimeField(blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.full_name

class Case(models.Model):
    owner=models.ForeignKey(User,on_delete=models.CASCADE,related_name="cases")
    assigned_detective=models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=True,related_name="assigned_cases")
    case_number=models.CharField(max_length=20,default=case_no,unique=True)
    title=models.CharField(max_length=200)
    description=models.TextField()
    case_type=models.CharField(max_length=5,choices=CASE_TYPE,db_index=True)
    category=models.CharField(max_length=6,choices=CATEGORY,db_index=True)
    status=models.CharField(max_length=13,choices=CASE_STATUS,default="OPEN",db_index=True)
    location=models.CharField(max_length=255)
    complaint_number=models.CharField(max_length=100,default="",blank=True,help_text="Police station complaint registered number (LOST cases only)")
    reward=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    def __str__(self): return self.case_number

class CaseImage(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name="images")
    image=models.ImageField(upload_to="case_images/",max_length=200)
    is_primary=models.BooleanField(default=False)
    clip_embedding=models.JSONField(blank=True,null=True)
    faiss_vector_id=models.BigIntegerField(blank=True,null=True)

class SightingReport(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name="sightings")
    reported_by=models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=True)
    reporter_name=models.CharField(max_length=100,blank=True)
    reporter_contact=models.CharField(max_length=100,blank=True)
    location=models.CharField(max_length=255)
    description=models.TextField()
    photo=models.ImageField(upload_to="sightings/",max_length=200,blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True)

class DetectiveRequest(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE)
    requested_by=models.ForeignKey(User,on_delete=models.CASCADE)
    requested_detective=models.ForeignKey(User,on_delete=models.SET_NULL,null=True,blank=True,related_name="detective_request_offers")
    message=models.TextField(blank=True)
    status=models.CharField(max_length=8,choices=REQUEST_STATUS,default="PENDING",db_index=True)
    admin_reason=models.TextField(blank=True,default="")
    reviewed_at=models.DateTimeField(blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True)

class CaseSolveRequest(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name="solve_requests")
    requested_by=models.ForeignKey(User,on_delete=models.CASCADE)
    previous_status=models.CharField(max_length=13,default="OPEN")
    message=models.TextField(blank=True,default="")
    status=models.CharField(max_length=8,choices=REQUEST_STATUS,default="PENDING",db_index=True)
    admin_reason=models.TextField(blank=True,default="")
    reviewed_at=models.DateTimeField(blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True)

class CaseAssignment(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE)
    detective=models.ForeignKey(User,on_delete=models.CASCADE,related_name="assignments")
    assigned_by=models.ForeignKey(User,on_delete=models.CASCADE,related_name="assigned")
    status=models.CharField(max_length=9,choices=ASSIGNMENT_STATUS,default="PENDING",db_index=True)
    assigned_at=models.DateTimeField(auto_now_add=True)
    reject_reason=models.TextField(blank=True,default="")
    rejected_at=models.DateTimeField(blank=True,null=True)

class InvestigationUpdate(models.Model):
    assignment = models.ForeignKey(
        CaseAssignment,
        on_delete=models.CASCADE,
        related_name="updates"
    )

    title = models.CharField(max_length=200)

    notes = models.TextField()

    evidence_photo = models.ImageField(
        upload_to="evidence/",
        max_length=200,
        blank=True,
        null=True
    )

    progress = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class DetectiveAchievement(models.Model):
    detective=models.ForeignKey(User,on_delete=models.CASCADE)
    case=models.ForeignKey(Case,on_delete=models.SET_NULL,null=True)
    title=models.CharField(max_length=200)
    description=models.TextField()
    image=models.ImageField(upload_to="achievements/",max_length=200,blank=True,null=True)
    created_at=models.DateTimeField(auto_now_add=True)

class Notification(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    case=models.ForeignKey(Case,on_delete=models.CASCADE,null=True,blank=True)
    title=models.CharField(max_length=200)
    message=models.TextField()
    is_read=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)

class Blog(models.Model):
    title=models.CharField(max_length=200)
    content=models.TextField()
    image=models.ImageField(upload_to="blog/",max_length=200,blank=True,null=True)
    author=models.ForeignKey(User,on_delete=models.CASCADE)
    created_at=models.DateTimeField(auto_now_add=True)

class Feedback(models.Model):
    sender=models.ForeignKey(User,on_delete=models.CASCADE)
    comment=models.TextField()
    admin_reply=models.TextField(blank=True)
    is_approved=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
