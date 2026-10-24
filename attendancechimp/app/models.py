"""
models.py — Data model for AttendanceChimp.

AttendanceChimp is an electronic class attendance tool.  Instructors create
courses and lectures; each lecture is assigned a random QR code.  Students
photograph the QR code and upload the image.  The system decodes the QR code,
matches it to a lecture, and records the attendance.

Entities
--------
- InstructorProfile : extends Django User -> instructor-specific data
- StudentProfile    : extends Django User -> student-specific data
- Course            : a course taught by one instructor
- Enrollment        : junction table linking students to courses (many-to-many)
- Lecture           : a single class meeting (has a QR-code identifier)
- QRCodeUpload      : a student's uploaded QR-code photograph

Why two profile tables instead of one Users table?
--------------------------------------------------
Django already has a built-in User table for login (username, password,
email).  Students and instructors need *different* extra fields, and other
tables should only point at the right kind of person: a Course must have an
instructor, and an upload must come from a student.  With two profile tables
the database itself enforces that (a ForeignKey to StudentProfile can never
point at an instructor), instead of relying on a "role" column being right.

Relationships
-------------
User              1 --- 1   InstructorProfile
User              1 --- 1   StudentProfile
InstructorProfile 1 --- *   Course
StudentProfile    * --- *   Course        (through Enrollment)
Course            1 --- *   Lecture
StudentProfile    1 --- *   QRCodeUpload
Lecture           1 --- *   QRCodeUpload  (optional: NULL until matched)

Anti-cheating ideas built into the schema
-----------------------------------------
- Each lecture gets its own random, unique QR code.
- Upload time is set by the server (auto_now_add), not by the student.
- A student can only have one upload per lecture (unique constraint).
- An upload only counts if the decoded code matches the lecture's code
  (see QRCodeUpload.check_code).
"""

from django.db import models
from django.contrib.auth.models import User

import logging
import string
import random

logger = logging.getLogger(__name__) # set up tracking, auditing, and debugging
                                     # allows using logger.warning(), logger.error(),
                                     # logger.info(), etc. Better than print() for debugging


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

class InstructorProfile(models.Model):
    """Extra information for a user who is an instructor.

    One-to-one with Django's built-in User: each instructor has exactly one
    login account. If the User is deleted, the profile is deleted too.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    department = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Instructor: {self.user.username}"


class StudentProfile(models.Model):
    """Extra information for a user who is a student.

    One-to-one with Django's built-in User. student_id is the university
    ID number and must be unique.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    student_id = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return f"Student: {self.user.username}"


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------

class Course(models.Model):
    """A course taught by exactly one instructor.

    meeting_days stores the weekdays as letters, e.g. "MWF" or "TR".
    on_delete=PROTECT means an instructor can't be deleted while they still
    teach a course, so attendance history isn't lost by accident.
    """
    name = models.CharField(max_length=200)                  # "Data Engineering"
    code = models.CharField(max_length=20, unique=True)      # "DATA 13600"
    instructor = models.ForeignKey(InstructorProfile, on_delete=models.PROTECT)
    meeting_days = models.CharField(max_length=7)            # "MWF", "TR", ...
    start_time = models.TimeField()
    end_time = models.TimeField()
    students = models.ManyToManyField(StudentProfile, through="Enrollment")

    def __str__(self):
        return f"{self.code}: {self.name}"


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class Enrollment(models.Model):
    """Junction table: which students are in which courses (many-to-many).

    One row = one student enrolled in one course. The unique constraint
    stops the same student from being enrolled in the same course twice.
    """
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "course"],
                                    name="unique_enrollment"),
        ]

    def __str__(self):
        return f"{self.student.user.username} in {self.course.code}"


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def generate_qr_code(length=12):
    """Return a random string of letters and digits to put in a QR code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


class Lecture(models.Model):
    """One class meeting of a course on a specific date.

    qr_code is a random identifier made automatically when the lecture is
    created; it is unique so a code from one lecture can't be reused for
    another. A course can have more than one lecture on the same date
    (e.g. a lab and a lecture), each with its own QR code.
    """
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    date = models.DateField()
    qr_code = models.CharField(max_length=32, unique=True, default=generate_qr_code)

    def attendance_count(self):
        """Number of students with a valid upload for this lecture."""
        return self.qrcodeupload_set.filter(is_valid=True).count()

    def __str__(self):
        return f"{self.course.code} on {self.date}"


# ---------------------------------------------------------------------------
# QR Code Upload
# ---------------------------------------------------------------------------

class QRCodeUpload(models.Model):
    """A photo of a QR code uploaded by a student.

    The lecture is optional (null=True): it stays empty until the photo is
    decoded and matched to a lecture. If that lecture is later deleted the
    upload is kept with lecture = NULL (SET_NULL) for auditing.
    uploaded_at is set by the server, so students can't fake the time.
    """
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE)
    lecture = models.ForeignKey(Lecture, on_delete=models.SET_NULL,
                                null=True, blank=True)
    image = models.FileField(upload_to="qr_uploads/")
    decoded_code = models.CharField(max_length=32, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_valid = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "lecture"],
                                    name="one_upload_per_student_per_lecture"),
        ]

    def check_code(self):
        """Mark the upload valid only if the decoded code matches the
        lecture's code AND the student is enrolled in that course."""
        if self.lecture is None:
            self.is_valid = False
        else:
            enrolled = Enrollment.objects.filter(
                student=self.student, course=self.lecture.course).exists()
            self.is_valid = enrolled and self.decoded_code == self.lecture.qr_code
            if not self.is_valid:
                logger.warning("Rejected upload %s from %s", self.pk, self.student)
        return self.is_valid

    def __str__(self):
        return f"Upload by {self.student.user.username} at {self.uploaded_at}"


# ---------------------------------------------------------------------------
# Helper / API functions
# ---------------------------------------------------------------------------

def get_attendance_rate(student, course):
    """Fraction of a course's lectures the student validly attended (0 to 1)."""
    total = Lecture.objects.filter(course=course).count()
    if total == 0:
        return 0.0
    attended = QRCodeUpload.objects.filter(
        student=student, lecture__course=course, is_valid=True).count()
    return attended / total
