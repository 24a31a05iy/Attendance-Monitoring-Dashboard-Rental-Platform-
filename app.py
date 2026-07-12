import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import Config
from datetime import datetime
import json
import pandas as pd

app = Flask(__name__)
app.config.from_object(Config)

# File Upload Configuration
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Association tables for teaching assignments
teacher_subject = db.Table('teacher_subject',
    db.Column('teacher_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('subject_id', db.Integer, db.ForeignKey('subject.id'), primary_key=True)
)

teacher_section = db.Table('teacher_section',
    db.Column('teacher_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('section_id', db.Integer, db.ForeignKey('section.id'), primary_key=True)
)

class Section(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, default=1)
    branch = db.Column(db.String(50), nullable=False)  # e.g., 'CSE', 'ECE'
    name = db.Column(db.String(10), nullable=False)    # e.g., 'A', 'B'
    users = db.relationship('User', backref='section', lazy=True)
    
    @property
    def display_name(self):
        suffix = 'th' if 11 <= self.year % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(self.year % 10, 'th')
        return f"{self.year}{suffix} Year - {self.branch} {self.name}"

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)   # e.g., 'Mathematics'
    code = db.Column(db.String(20), unique=True)
    attendances = db.relationship('Attendance', backref='subject', lazy=True)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'STUDENT', 'TEACHER', 'ADMIN'
    
    # Student specific
    section_id = db.Column(db.Integer, db.ForeignKey('section.id'))
    
    # Teacher specific
    is_class_teacher = db.Column(db.Boolean, default=False)
    subjects_taught = db.relationship('Subject', secondary=teacher_subject, lazy='subquery',
        backref=db.backref('teachers', lazy=True))
    sections_taught = db.relationship('Section', secondary=teacher_section, lazy='subquery',
        backref=db.backref('teachers', lazy=True))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False)  # 'PRESENT', 'ABSENT'

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_messages')

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
    
def get_setting(key, default=''):
    setting = Setting.query.filter_by(key=key).first()
    return setting.value if setting else default

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Wrong username or password!')
        
    custom_settings = {
        'college_name': get_setting('college_name', 'Pragati Engineering College'),
        'welcome_text': get_setting('welcome_text', 'Welcome back to your comprehensive attendance dashboard.'),
        'logo_path': get_setting('logo_path', 'https://cdn-icons-png.flaticon.com/512/3228/3228913.png'),
        'hero_image_path': get_setting('hero_image_path', '')
    }
    return render_template('login.html', settings=custom_settings)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    subjects = Subject.query.all()
    selected_subject_id = request.args.get('subject_id')
    
    if current_user.role == 'STUDENT':
        query = Attendance.query.filter_by(student_id=current_user.id)
        if selected_subject_id:
            query = query.filter_by(subject_id=selected_subject_id)
        attendances = query.all()
    elif current_user.role == 'TEACHER':
        # Default view: See attendance for their subjects
        subject_ids = [s.id for s in current_user.subjects_taught]
        attendances = Attendance.query.filter(Attendance.subject_id.in_(subject_ids)).order_by(Attendance.date.desc()).limit(20).all()
    else:  # ADMIN
        attendances = Attendance.query.order_by(Attendance.date.desc()).limit(50).all()
    
    chart_data = get_chart_data(current_user, selected_subject_id)
    threshold = float(get_setting('attendance_threshold', '75'))
    
    low_attendance_students = []
    unread_messages = []
    
    if current_user.role in ['TEACHER', 'ADMIN']:
        # Check students they have access to
        if current_user.role == 'ADMIN':
            students = User.query.filter_by(role='STUDENT').all()
        else:
            students = [s for sec in current_user.sections_taught for s in sec.users if s.role == 'STUDENT']
            
        for student in students:
            # calculate overall presence
            records = Attendance.query.filter_by(student_id=student.id).all()
            if records:
                present = len([r for r in records if r.status == 'PRESENT'])
                pct = present / len(records) * 100
                if pct < threshold:
                    low_attendance_students.append({'student': student, 'pct': round(pct, 1)})
    
    if current_user.role == 'STUDENT':
        unread_messages = Message.query.filter_by(receiver_id=current_user.id, is_read=False).order_by(Message.timestamp.desc()).all()
        if chart_data['total'] > 0:
            chart_data['is_low'] = chart_data['pct'] < threshold

    return render_template('dashboard.html', 
                          attendances=attendances, 
                          chart_data=chart_data,
                          subjects=subjects,
                          selected_subject_id=selected_subject_id,
                          low_attendance_students=low_attendance_students,
                          unread_messages=unread_messages,
                          threshold=threshold)

@app.route('/view_student/<int:student_id>', methods=['GET'])
@login_required
def view_student(student_id):
    if current_user.role == 'STUDENT':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    student = User.query.filter_by(role='STUDENT', id=student_id).first_or_404()
    
    # Check permissions
    if current_user.role == 'TEACHER':
        # Can teacher see this student? Must be in their sections.
        if student.section not in current_user.sections_taught:
            flash('You do not teach this student.', 'danger')
            return redirect(url_for('dashboard'))
            
    subjects = Subject.query.all()
    attendance_data = []
    threshold = float(get_setting('attendance_threshold', '75'))
    
    for subject in subjects:
        records = Attendance.query.filter_by(student_id=student_id, subject_id=subject.id).all()
        if not records:
            continue
        present = len([r for r in records if r.status == 'PRESENT'])
        total = len(records)
        pct = round(present/total*100, 1) if total else 0
        attendance_data.append({
            'subject': subject,
            'present': present,
            'total': total,
            'pct': pct,
            'low': pct < threshold
        })
        
    return render_template('view_student.html', student=student, attendance_data=attendance_data, threshold=threshold)

@app.route('/send_message/<int:student_id>', methods=['POST'])
@login_required
def send_message(student_id):
    if current_user.role == 'STUDENT':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    student = User.query.filter_by(role='STUDENT', id=student_id).first_or_404()
    content = request.form.get('content')
    if content:
        msg = Message(sender_id=current_user.id, receiver_id=student.id, content=content)
        db.session.add(msg)
        db.session.commit()
        flash('Message sent successfully!', 'success')
        
    return redirect(url_for('view_student', student_id=student_id))

@app.route('/read_message/<int:message_id>', methods=['POST'])
@login_required
def read_message(message_id):
    msg = Message.query.get_or_404(message_id)
    if msg.receiver_id == current_user.id:
        msg.is_read = True
        db.session.commit()
    return redirect(url_for('dashboard'))

def get_chart_data(user, subject_id=None):
    if user.role == 'STUDENT':
        query = Attendance.query.filter_by(student_id=user.id)
        if subject_id:
            query = query.filter_by(subject_id=subject_id)
        data = query.all()
        present = len([a for a in data if a.status == 'PRESENT'])
        total = len(data)
        return {'present': present, 'total': total, 'pct': round(present/total*100, 1) if total else 0}
    return {'present': 0, 'total': 0}

@app.route('/take_attendance', methods=['GET', 'POST'])
@login_required
def take_attendance():
    if current_user.role == 'STUDENT':
        flash('Unauthorized access')
        return redirect(url_for('dashboard'))
    
    # Get subjects and sections the teacher can access
    if current_user.role == 'ADMIN':
        subjects = Subject.query.all()
        sections = Section.query.all()
    else: # TEACHER
        subjects = current_user.subjects_taught
        sections = current_user.sections_taught
        
    selected_subject_id = request.args.get('subject_id')
    selected_section_id = request.args.get('section_id')
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    students = []
    
    # Verify access again for security if they selected something
    if selected_section_id and selected_subject_id and current_user.role != 'ADMIN':
        valid_subject = any(str(s.id) == selected_subject_id for s in subjects)
        valid_section = any(str(s.id) == selected_section_id for s in sections)
        
        if current_user.is_class_teacher and valid_section:
            valid_subject = True
            
        if not valid_subject or not valid_section:
            flash('You do not have permission to take attendance for this subject/section', 'danger')
            return redirect(url_for('take_attendance'))

    if selected_section_id:
        students = User.query.filter_by(role='STUDENT', section_id=selected_section_id).all()
        
    if request.method == 'POST':
        if not selected_subject_id or not selected_section_id:
            flash('Please load a class first', 'danger')
            return redirect(url_for('take_attendance'))

        for student in students:
            status = request.form.get(f'status_{student.id}')
            if status:
                existing = Attendance.query.filter_by(
                    student_id=student.id,
                    subject_id=selected_subject_id,
                    date=date_obj
                ).first()
                if existing:
                    existing.status = status
                else:
                    new_attendance = Attendance(
                        student_id=student.id,
                        subject_id=selected_subject_id,
                        date=date_obj,
                        status=status
                    )
                    db.session.add(new_attendance)
        db.session.commit()
        flash('Attendance saved successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('take_attendance.html', 
                          subjects=subjects, 
                          sections=sections, 
                          students=students,
                          selected_subject_id=selected_subject_id,
                          selected_section_id=selected_section_id,
                          date_str=date_str)

# ----------------- ADMIN USER MANAGEMENT ----------------- #
@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    year = request.args.get('year', type=int)
    branch = request.args.get('branch')
    section_id = request.args.get('section_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    role = request.args.get('role')
    
    users = User.query.all()
    
    if role:
        users = [u for u in users if u.role == role]
        
    if subject_id:
        sub = Subject.query.get(subject_id)
        if sub:
            users = [u for u in users if u.role == 'TEACHER' and sub in u.subjects_taught]
            
    if section_id:
        users = [u for u in users if (u.role == 'STUDENT' and u.section_id == section_id) or (u.role == 'TEACHER' and any(s.id == section_id for s in u.sections_taught))]
    elif branch:
        users = [u for u in users if (u.role == 'STUDENT' and u.section and u.section.branch == branch) or (u.role == 'TEACHER' and any(s.branch == branch for s in u.sections_taught))]
    elif year:
        users = [u for u in users if (u.role == 'STUDENT' and u.section and u.section.year == year) or (u.role == 'TEACHER' and any(s.year == year for s in u.sections_taught))]
        
    sections = Section.query.all()
    all_years = sorted(list(set([s.year for s in sections])))
    all_branches = sorted(list(set([s.branch for s in sections])))
    subjects = Subject.query.all()

    return render_template('admin/users.html', 
                           users=users,
                           sections=sections,
                           all_years=all_years,
                           all_branches=all_branches,
                           subjects=subjects,
                           year_filter=year,
                           branch_filter=branch,
                           section_filter=section_id,
                           subject_filter=subject_id,
                           role_filter=role)

@app.route('/admin/users/create', methods=['GET', 'POST'])
@login_required
def admin_create_user():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))

    subjects = Subject.query.all()
    sections = Section.query.all()

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('admin_create_user'))

        new_user = User(username=username, role=role)
        new_user.set_password(password)

        if role == 'STUDENT':
            new_user.section_id = request.form.get('section_id')
        elif role == 'TEACHER':
            new_user.is_class_teacher = bool(request.form.get('is_class_teacher'))
            
            # Map selected subjects
            subject_ids = request.form.getlist('subjects')
            for sid in subject_ids:
                sub = Subject.query.get(sid)
                if sub: new_user.subjects_taught.append(sub)
                
            # Map selected sections
            section_ids = request.form.getlist('sections')
            for sid in section_ids:
                sec = Section.query.get(sid)
                if sec: new_user.sections_taught.append(sec)

        db.session.add(new_user)
        db.session.commit()
        flash(f'{role.capitalize()} profile created successfully!', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/user_form.html', subjects=subjects, sections=sections)

@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_user(user_id):
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    subjects = Subject.query.all()
    sections = Section.query.all()

    if request.method == 'POST':
        user.username = request.form.get('username')
        new_password = request.form.get('new_password')
        
        if new_password: # Direct admin bypass
            user.set_password(new_password)

        if user.role == 'STUDENT':
            user.section_id = request.form.get('section_id')
        elif user.role == 'TEACHER':
            user.is_class_teacher = bool(request.form.get('is_class_teacher'))
            
            # Rebuild mappings completely
            user.subjects_taught = []
            user.sections_taught = []
            
            subject_ids = request.form.getlist('subjects')
            for sid in subject_ids:
                sub = Subject.query.get(sid)
                if sub: user.subjects_taught.append(sub)
                
            section_ids = request.form.getlist('sections')
            for sid in section_ids:
                sec = Section.query.get(sid)
                if sec: user.sections_taught.append(sec)

        db.session.commit()
        flash(f'{user.username}\'s profile updated successfully!', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/user_form.html', user=user, subjects=subjects, sections=sections)

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own administrative account.', 'danger')
        return redirect(url_for('admin_users'))

    # Optional: Delete associated attendance rows if necessary, or let them cascade
    if user.role == 'STUDENT':
        Attendance.query.filter_by(student_id=user.id).delete()
    elif user.role == 'TEACHER':
        user.subjects_taught = []
        user.sections_taught = []
    
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted entirely.', 'success')
    return redirect(url_for('admin_users'))

# ----------------- ADMIN SETTINGS ROUTE ----------------- #
@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Textual Updates
        college_name = request.form.get('college_name')
        welcome_text = request.form.get('welcome_text')
        attendance_threshold = request.form.get('attendance_threshold')
        
        def update_setting(key, val):
            s = Setting.query.filter_by(key=key).first()
            if s:
                s.value = val
            elif val:
                db.session.add(Setting(key=key, value=val))

        if college_name is not None: update_setting('college_name', college_name)
        if welcome_text is not None: update_setting('welcome_text', welcome_text)
        if attendance_threshold is not None: update_setting('attendance_threshold', attendance_threshold)

        # Image Upload Handling (Logo/Hero Image)
        logo = request.files.get('logo_image')
        hero = request.files.get('hero_image')
        
        def save_file_setting(file_obj, key):
            if file_obj and file_obj.filename:
                filename = secure_filename(file_obj.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file_obj.save(filepath)
                # Store relative URL
                update_setting(key, url_for('static', filename='uploads/' + filename))
                
        save_file_setting(logo, 'logo_path')
        save_file_setting(hero, 'hero_image_path')
        
        db.session.commit()
        flash('Settings successfully updated!', 'success')
        return redirect(url_for('admin_settings'))

    custom_settings = {
        'college_name': get_setting('college_name', 'Pragati Engineering College'),
        'welcome_text': get_setting('welcome_text', 'Welcome back to your comprehensive attendance dashboard.'),
        'attendance_threshold': get_setting('attendance_threshold', '75'),
        'logo_path': get_setting('logo_path', 'https://cdn-icons-png.flaticon.com/512/3228/3228913.png'),
        'hero_image_path': get_setting('hero_image_path', '')
    }
    
    return render_template('admin/settings.html', settings=custom_settings)

@app.route('/create_test_data')
@login_required
def create_test_data():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    if User.query.first():
        return 'Data exists. Delete app.db to recreate.'
    # Create Branches and Sections
    sec_cse_a = Section(year=1, branch='CSE', name='A')
    sec_cse_b = Section(year=1, branch='CSE', name='B')
    db.session.add_all([sec_cse_a, sec_cse_b])
    
    # Create Subjects
    sub_maths = Subject(name='Mathematics', code='MTH101')
    sub_physics = Subject(name='Physics', code='PHY101')
    sub_cs = Subject(name='Computer Science', code='CS101')
    db.session.add_all([sub_maths, sub_physics, sub_cs])
    
    # Must commit to get IDs for relationships
    db.session.commit()
    
    # 5 Students
    students = []
    # 3 in CSE-A
    for i in range(1, 4):
        s = User(username=f'student{i}', role='STUDENT', section_id=sec_cse_a.id)
        s.set_password('123456')
        students.append(s)
    # 2 in CSE-B
    for i in range(4, 6):
        s = User(username=f'student{i}', role='STUDENT', section_id=sec_cse_b.id)
        s.set_password('123456')
        students.append(s)
    db.session.add_all(students)
    
    # Admin
    admin = User(username='admin', role='ADMIN')
    admin.set_password('123456')
    db.session.add(admin)

    # Maths Teacher (teaches both sections)
    t_maths = User(username='teacher_maths', role='TEACHER')
    t_maths.set_password('123456')
    t_maths.subjects_taught.append(sub_maths)
    t_maths.sections_taught.extend([sec_cse_a, sec_cse_b])
    db.session.add(t_maths)

    # Physics Teacher (Class teacher for CSE-A)
    t_physics = User(username='teacher_physics', role='TEACHER', is_class_teacher=True)
    t_physics.set_password('123456')
    t_physics.subjects_taught.append(sub_physics)
    t_physics.sections_taught.append(sec_cse_a)
    db.session.add(t_physics)

    db.session.commit()

    # Create dummy attendance for student1 (CSE-A) in Maths
    for i in range(5):
        att = Attendance(
            student_id=students[0].id, 
            subject_id=sub_maths.id,
            date=datetime.now().date(),
            status='PRESENT' if i % 4 else 'ABSENT'
        )
        db.session.add(att)
    
    db.session.commit()
    return 'Detailed Test Data Created! Logins available: admin, teacher_maths, teacher_physics, student1 to student5 (password: 123456)'

# ----------------- ADMIN SECTION MANAGEMENT ----------------- #
@app.route('/admin/sections', methods=['GET', 'POST'])
@login_required
def admin_sections():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        section_year = request.form.get('section_year', type=int)
        section_branch = request.form.get('section_branch')
        section_name = request.form.get('section_name')
        if section_year and section_branch and section_name:
            if Section.query.filter_by(year=section_year, branch=section_branch, name=section_name).first():
                flash(f'Class {section_year} Year - {section_branch} {section_name} already exists!', 'warning')
            else:
                new_section = Section(year=section_year, branch=section_branch, name=section_name)
                db.session.add(new_section)
                db.session.commit()
                flash(f'Class successfully added: {new_section.display_name}', 'success')
        return redirect(url_for('admin_sections'))
        
    sections = Section.query.all()
    return render_template('admin/sections.html', sections=sections)

@app.route('/admin/sections/<int:section_id>/delete', methods=['POST'])
@login_required
def admin_delete_section(section_id):
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    section = Section.query.get_or_404(section_id)
    # Check if section has students assigned
    students_in_section = User.query.filter_by(section_id=section.id).count()
    if students_in_section > 0:
        flash(f'Cannot delete {section.display_name} as {students_in_section} students are assigned here.', 'danger')
    else:
        # Clear many-to-many relationship with teachers safely before DB deletion
        section.teachers = []
        db.session.delete(section)
        db.session.commit()
        flash(f'Class Section {section.display_name} deleted successfully.', 'success')
        
    return redirect(url_for('admin_sections'))

# ----------------- ADMIN SUBJECT MANAGEMENT ----------------- #
@app.route('/admin/subjects', methods=['GET', 'POST'])
@login_required
def admin_subjects():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        subject_name = request.form.get('subject_name')
        subject_code = request.form.get('subject_code')
        if subject_name and subject_code:
            if Subject.query.filter_by(code=subject_code).first():
                flash(f'Subject with code {subject_code} already exists!', 'warning')
            else:
                new_subject = Subject(name=subject_name, code=subject_code)
                db.session.add(new_subject)
                db.session.commit()
                flash(f'Subject successfully added: {subject_name} ({subject_code})', 'success')
        return redirect(url_for('admin_subjects'))
        
    subjects = Subject.query.all()
    return render_template('admin/subjects.html', subjects=subjects)

@app.route('/admin/subjects/<int:subject_id>/delete', methods=['POST'])
@login_required
def admin_delete_subject(subject_id):
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    subject = Subject.query.get_or_404(subject_id)
    
    # Check if subject has attendances recorded
    attendances_count = Attendance.query.filter_by(subject_id=subject.id).count()
    if attendances_count > 0:
        flash(f'Cannot delete {subject.name} because it has {attendances_count} attendance records.', 'danger')
    else:
        # Clear many-to-many relationship with teachers safely before DB deletion
        subject.teachers = []
        db.session.delete(subject)
        db.session.commit()
        flash(f'Subject {subject.name} deleted successfully.', 'success')
        
    return redirect(url_for('admin_subjects'))

# ----------------- NEW FEATURES: EXCEL & REPORTS ----------------- #
import io
from flask import send_file

@app.route('/admin/users/import', methods=['GET', 'POST'])
@login_required
def admin_import_users():
    if current_user.role != 'ADMIN':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
            
        if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls') or file.filename.endswith('.csv')):
            try:
                if file.filename.endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    df = pd.read_excel(file)
                    
                success_count = 0
                error_msgs = []
                
                for index, row in df.iterrows():
                    try:
                        username = str(row.get('Username', '')).strip()
                        password = str(row.get('Password', '')).strip()
                        role = str(row.get('Role', '')).strip().upper()
                        
                        if not username or not password or not role or username == 'nan':
                            continue
                            
                        # Check if user exists
                        if User.query.filter_by(username=username).first():
                            error_msgs.append(f"Row {index+2}: Username '{username}' already exists.")
                            continue
                            
                        new_user = User(username=username, role=role)
                        new_user.set_password(password)
                        
                        if role != 'STUDENT':
                            error_msgs.append(f"Row {index+2}: Only STUDENT accounts can be mass imported here.")
                            continue

                        new_user = User(username=username, role=role)
                        new_user.set_password(password)
                        
                        year = row.get('Year')
                        branch = str(row.get('Branch', '')).strip()
                        name = str(row.get('Section Name', '')).strip()
                        
                        if pd.isna(year) or not str(year).strip() or not branch or not name or branch == 'nan':
                            error_msgs.append(f"Row {index+2}: Missing Year/Branch/Section Name.")
                            continue
                            
                        branch = branch.upper()
                        name = name.upper()
                        
                        section = Section.query.filter_by(year=int(year), branch=branch, name=name).first()
                        if not section:
                            section = Section(year=int(year), branch=branch, name=name)
                            db.session.add(section)
                            db.session.commit()
                            
                        new_user.section_id = section.id
                            
                        db.session.add(new_user)
                        db.session.commit()
                        success_count += 1
                        
                    except Exception as e:
                        db.session.rollback()
                        error_msgs.append(f"Row {index+2} Error: {str(e)}")
                        
                if success_count > 0:
                    flash(f'Successfully imported {success_count} users!', 'success')
                if error_msgs:
                    for err in error_msgs[:5]: # show max 5 errors
                        flash(err, 'danger')
                    if len(error_msgs) > 5:
                        flash(f'...and {len(error_msgs)-5} more errors.', 'danger')
                        
                return redirect(url_for('admin_users'))
                
            except Exception as e:
                flash(f'Error processing file: {str(e)}', 'danger')
                return redirect(request.url)
                
    return render_template('admin/import_users.html')

@app.route('/attendance/report', methods=['GET'])
@login_required
def attendance_report():
    if current_user.role == 'STUDENT':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    year_filter = request.args.get('year', type=int)
    branch_filter = request.args.get('branch')
    section_filter = request.args.get('section_id', type=int)
    subject_filter = request.args.get('subject_id', type=int)
    
    # Query attendance on that day
    query = Attendance.query.filter_by(date=date_obj)
    if subject_filter:
        query = query.filter_by(subject_id=subject_filter)
        
    attendances_today = query.all()
    
    section_stats = {}
    sections = Section.query.all()
    
    # Filter sections for calculating stats based on year, branch, section_id
    filtered_sections = sections
    if year_filter:
        filtered_sections = [s for s in filtered_sections if s.year == year_filter]
    if branch_filter:
        filtered_sections = [s for s in filtered_sections if s.branch == branch_filter]
    if section_filter:
        filtered_sections = [s for s in filtered_sections if s.id == section_filter]
        
    for s in filtered_sections:
        section_stats[s.id] = {
            'display': s.display_name,
            'year': s.year,
            'branch': s.branch,
            'name': s.name,
            'present': 0,
            'absent': 0,
            'total': 0
        }
        
    overall_present = 0
    overall_absent = 0
    
    student_presence = {}
    for att in attendances_today:
        if att.student_id not in student_presence:
            student_presence[att.student_id] = False
        if att.status == 'PRESENT':
            student_presence[att.student_id] = True
            
    for st_id, is_present in student_presence.items():
        user = User.query.get(st_id)
        if user and user.section_id:
            s_id = user.section_id
            if s_id in section_stats:
                if is_present:
                    section_stats[s_id]['present'] += 1
                    overall_present += 1
                else:
                    section_stats[s_id]['absent'] += 1
                    overall_absent += 1
                section_stats[s_id]['total'] += 1
                
    active_sections = [s for s in section_stats.values() if s['total'] > 0]
    
    # Sort by year, branch, section name
    active_sections.sort(key=lambda x: (x['year'], x['branch'], x['name']))
    
    overall_total = overall_present + overall_absent
    pct = round((overall_present / overall_total * 100), 1) if overall_total > 0 else 0
    
    all_years = sorted(list(set([s.year for s in sections])))
    all_branches = sorted(list(set([s.branch for s in sections])))
    all_subjects = Subject.query.all()
    
    return render_template('attendance_report.html', 
                          date_str=date_str, 
                          active_sections=active_sections,
                          overall_present=overall_present,
                          overall_total=overall_total,
                          pct=pct,
                          all_years=all_years,
                          all_branches=all_branches,
                          all_sections=sections,
                          all_subjects=all_subjects,
                          year_filter=year_filter,
                          branch_filter=branch_filter,
                          section_filter=section_filter,
                          subject_filter=subject_filter)

@app.route('/attendance/export', methods=['GET', 'POST'])
@login_required
def attendance_export():
    if current_user.role == 'STUDENT':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        section_id = request.form.get('section_id', type=int)
        subject_id = request.form.get('subject_id', type=int)
        year = request.form.get('year', type=int)
        branch = request.form.get('branch')
        
        if not subject_id:
            flash('Please select a subject.', 'warning')
            return redirect(url_for('attendance_export'))
            
        subject = Subject.query.get_or_404(subject_id)
        
        # Determine which sections to export based on filters
        sections_to_export = []
        if section_id:
            s = Section.query.get(section_id)
            if s: sections_to_export = [s]
        else:
            q = Section.query
            if year: q = q.filter_by(year=year)
            if branch: q = q.filter_by(branch=branch)
            sections_to_export = q.all()
            if not sections_to_export:
                flash('No sections found for this filter.', 'warning')
                return redirect(url_for('attendance_export'))
                
        # Verify teacher can export these sections&subject
        if current_user.role == 'TEACHER':
            allowed_sections = set(current_user.sections_taught)
            if not current_user.is_class_teacher:
                sections_to_export = [s for s in sections_to_export if s in allowed_sections and subject in getattr(current_user, 'subjects_taught', [])]
            else:
                sections_to_export = [s for s in sections_to_export if s in allowed_sections]
                
            if not sections_to_export:
                flash('You are not authorized to export data for these classes/subjects.', 'danger')
                return redirect(url_for('attendance_export'))
        
        student_ids = []
        for sec in sections_to_export:
            students_in_sec = User.query.filter_by(role='STUDENT', section_id=sec.id).all()
            student_ids.extend([s.id for s in students_in_sec])
            
        if not student_ids:
            flash('No students in these sections.', 'warning')
            return redirect(url_for('attendance_export'))
            
        students = User.query.filter(User.id.in_(student_ids)).all()
        # Sort by year, branch, section name, then username
        students.sort(key=lambda x: (x.section.year if x.section else 0, x.section.branch if x.section else '', x.section.name if x.section else '', x.username))
        
        attendances = Attendance.query.filter(Attendance.subject_id==subject.id, Attendance.student_id.in_(student_ids)).order_by(Attendance.date).all()
        
        dates = sorted(list(set([a.date.strftime('%Y-%m-%d') for a in attendances])))
        
        data = []
        for st in students:
            row = {
                'Year': st.section.year if st.section else '',
                'Branch': st.section.branch if st.section else '',
                'Section': st.section.name if st.section else '',
                'Username (ID)': st.username,
                'Name': st.username 
            }
            present_count = 0
            
            st_atts = {a.date.strftime('%Y-%m-%d'): a.status for a in attendances if a.student_id == st.id}
            
            for d in dates:
                status = st_atts.get(d, 'N/A')
                row[d] = status
                if status == 'PRESENT':
                    present_count += 1
                    
            row['Total Present'] = present_count
            row['Total Classes'] = len(dates)
            row['Attendance %'] = round((present_count/len(dates)*100), 1) if len(dates) > 0 else 0
            
            data.append(row)
            
        if not data:
            flash('No attendance data available.', 'warning')
            return redirect(url_for('attendance_export'))

        df = pd.DataFrame(data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance')
            
        output.seek(0)
        
        if len(sections_to_export) == 1:
            sec = sections_to_export[0]
            filename = f"Attendance_Year{sec.year}_{sec.branch}_{sec.name}_{subject.code}.xlsx"
        else:
            filename = f"Attendance_Filtered_{subject.code}.xlsx"
        
        return send_file(output, download_name=filename, as_attachment=True)
        
    if current_user.role == 'ADMIN':
        subjects = Subject.query.all()
        sections = Section.query.all()
    else:
        subjects = current_user.subjects_taught
        sections = current_user.sections_taught
        
    all_years = sorted(list(set([s.year for s in sections])))
    all_branches = sorted(list(set([s.branch for s in sections])))
        
    return render_template('export_attendance.html', subjects=subjects, sections=sections, all_years=all_years, all_branches=all_branches)

@app.route('/irregular_students')
@login_required
def irregular_students():
    if current_user.role == 'STUDENT':
        flash('Unauthorized Access', 'danger')
        return redirect(url_for('dashboard'))
        
    year = request.args.get('year', type=int)
    branch = request.args.get('branch')
    section_id = request.args.get('section_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    sort_order = request.args.get('sort_order', 'asc')  # 'asc' or 'desc'
    
    threshold = float(get_setting('attendance_threshold', '75'))
    
    # Base query for students
    if current_user.role == 'ADMIN':
        sections_allowed = Section.query.all()
    else:
        sections_allowed = current_user.sections_taught
        
    # Apply filters to sections
    filtered_sections = sections_allowed
    if year:
        filtered_sections = [s for s in filtered_sections if s.year == year]
    if branch:
        filtered_sections = [s for s in filtered_sections if s.branch == branch]
    if section_id:
        filtered_sections = [s for s in filtered_sections if s.id == section_id]
        
    students = []
    for sec in filtered_sections:
        students.extend([u for u in sec.users if u.role == 'STUDENT'])
        
    irregular_list = []
    for student in students:
        # If subject is specified, filter records by subject
        q = Attendance.query.filter_by(student_id=student.id)
        if subject_id:
            q = q.filter_by(subject_id=subject_id)
        records = q.all()
        
        if records:
            present = len([r for r in records if r.status == 'PRESENT'])
            total = len(records)
            pct = round(present / total * 100, 1) if total > 0 else 0
            if pct < threshold:
                irregular_list.append({
                    'student': student,
                    'present': present,
                    'total': total,
                    'pct': pct
                })
                
    # Sort
    if sort_order == 'desc':
        irregular_list.sort(key=lambda x: x['pct'], reverse=True)
    else:
        irregular_list.sort(key=lambda x: x['pct'])
        
    all_years = sorted(list(set([s.year for s in sections_allowed])))
    all_branches = sorted(list(set([s.branch for s in sections_allowed])))
    if current_user.role == 'ADMIN':
        all_subjects = Subject.query.all()
    else:
        all_subjects = current_user.subjects_taught
        
    return render_template('irregular_students.html',
                           irregular_list=irregular_list,
                           threshold=threshold,
                           sections=sections_allowed,
                           all_years=all_years,
                           all_branches=all_branches,
                           subjects=all_subjects,
                           year_filter=year,
                           branch_filter=branch,
                           section_filter=section_id,
                           subject_filter=subject_id,
                           sort_order=sort_order)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
