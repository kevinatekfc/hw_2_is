# =============================================================================
# robot-models.py — LLM-generated data model for AttendanceChimp
#
# LLM: Claude (Anthropic), model Claude Opus 5.5, via claude.ai
#
# Prompt used:
#   "Write a Django models.py for an app called AttendanceChimp that tracks
#    class attendance with QR codes. It needs InstructorProfile and
#    StudentProfile tables linked to Django's User, a Course table (name,
#    instructor, meeting schedule), an Enrollment table linking students to
#    courses (many-to-many), a Lecture table linked to a course with a date and
#    a QR identifier, and a QRCodeUpload table linked to a student and
#    (optionally) a lecture. The design should be simple to use, support
#    attendance analytics, and be robust to cheating. Emphasize anti-cheating
#    and analytics features, and write it the way a production codebase would."
# =============================================================================

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base that adds created/updated timestamps to every table."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class InstructorProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="instructor_profile")
    department = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="student_profile")
    student_number = models.CharField(max_length=32, unique=True)
    # Anti-cheating: remember the device the student registered with.
    registered_device_id = models.CharField(max_length=128, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.student_number})"


class Course(TimeStampedModel):
    class Term(models.TextChoices):
        AUTUMN = "AUT", "Autumn"
        WINTER = "WIN", "Winter"
        SPRING = "SPR", "Spring"
        SUMMER = "SUM", "Summer"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    term = models.CharField(max_length=3, choices=Term.choices)
    year = models.PositiveSmallIntegerField()
    instructor = models.ForeignKey(InstructorProfile, on_delete=models.PROTECT,
                                   related_name="courses")
    students = models.ManyToManyField(StudentProfile, through="Enrollment",
                                      related_name="courses")
    location = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["code", "term", "year"],
                                    name="unique_course_offering"),
        ]

    def __str__(self):
        return f"{self.code} {self.term} {self.year}"


class MeetingSchedule(models.Model):
    """One weekly meeting slot of a course (a course can have several)."""
    class Weekday(models.IntegerChoices):
        MON = 0, "Monday"
        TUE = 1, "Tuesday"
        WED = 2, "Wednesday"
        THU = 3, "Thursday"
        FRI = 4, "Friday"
        SAT = 5, "Saturday"
        SUN = 6, "Sunday"

    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name="meeting_slots")
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(end_time__gt=models.F("start_time")),
                                   name="meeting_end_after_start"),
        ]


class Enrollment(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DROPPED = "dropped", "Dropped"
        AUDIT = "audit", "Audit"

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE,
                                related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name="enrollments")
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "course"],
                                    name="unique_student_course"),
        ]
        indexes = [models.Index(fields=["course", "status"])]


class Lecture(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name="lectures")
    date = models.DateField()
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Anti-cheating: the code is only accepted inside this window.
    qr_valid_from = models.DateTimeField(null=True, blank=True)
    qr_valid_until = models.DateTimeField(null=True, blank=True)
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["date"]
        indexes = [models.Index(fields=["course", "date"])]

    def open_qr_window(self, minutes=15):
        now = timezone.now()
        self.qr_valid_from = now
        self.qr_valid_until = now + timedelta(minutes=minutes)
        self.save(update_fields=["qr_valid_from", "qr_valid_until", "updated_at"])

    def rotate_qr_token(self):
        """Issue a new token, e.g. if a photo of the code leaks."""
        self.qr_token = uuid.uuid4()
        self.save(update_fields=["qr_token", "updated_at"])

    def is_qr_active(self, at=None):
        at = at or timezone.now()
        return bool(self.qr_valid_from and self.qr_valid_until
                    and self.qr_valid_from <= at <= self.qr_valid_until)

    def attendance_rate(self):
        enrolled = self.course.enrollments.filter(
            status=Enrollment.Status.ACTIVE).count()
        if not enrolled:
            return 0.0
        present = self.uploads.filter(
            status=QRCodeUpload.Status.ACCEPTED).values("student").distinct().count()
        return present / enrolled


class QRCodeUpload(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE,
                                related_name="uploads")
    lecture = models.ForeignKey(Lecture, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="uploads")
    image = models.FileField(upload_to="qr_uploads/%Y/%m/%d/")
    image_sha256 = models.CharField(max_length=64, db_index=True, blank=True)
    decoded_token = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)
    rejection_reason = models.CharField(max_length=200, blank=True)
    # Anti-cheating metadata captured at upload time.
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_id = models.CharField(max_length=128, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "lecture"],
                condition=models.Q(status="accepted"),
                name="one_accepted_upload_per_lecture"),
        ]
        indexes = [models.Index(fields=["lecture", "status"])]

    def validate(self):
        """Accept or reject this upload; returns True if accepted."""
        if self.lecture is None:
            return self._reject("No matching lecture")
        if str(self.lecture.qr_token) != self.decoded_token:
            return self._reject("Token mismatch")
        if not self.lecture.is_qr_active(self.created_at):
            return self._reject("Outside QR window")
        if not Enrollment.objects.filter(student=self.student,
                                         course=self.lecture.course,
                                         status=Enrollment.Status.ACTIVE).exists():
            return self._reject("Not enrolled")
        if self.image_sha256 and QRCodeUpload.objects.filter(
                image_sha256=self.image_sha256).exclude(pk=self.pk).exists():
            return self._reject("Duplicate image (possible shared photo)")
        self.status = self.Status.ACCEPTED
        self.save(update_fields=["status", "updated_at"])
        return True

    def _reject(self, reason):
        self.status = self.Status.REJECTED
        self.rejection_reason = reason
        self.save(update_fields=["status", "rejection_reason", "updated_at"])
        return False
