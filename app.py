import math
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, flash
import os
import MySQLdb.cursors
import json
import time
import cv2
import keyboard
import base64
import requests
import utils
from flask_mysqldb import MySQL
from utils import cheat_Detection1, cheat_Detection2, cheat_Detection3
from utils import add_violation, get_current_student, get_current_exam
from werkzeug.security import generate_password_hash, check_password_hash


# -------------------------------
# Global Variables
# -------------------------------
studentInfo = None
profileName = None

# -------------------------------
# Flask App Configuration
# -------------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.urandom(24)  # Secure random secret key

# MySQL Database Configuration
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'admin'
app.config['MYSQL_DB'] = 'examproctordb'
app.config['MYSQL_PORT'] = 3306


# Optional: enable automatic reconnect
app.config['MYSQL_CONNECT_TIMEOUT'] = 28800   # 8 hours
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)


@app.teardown_appcontext
def cleanup(exception):
    utils.release_resources()


import utils
utils.init_mysql_utils(app, mysql)

# -------------------------------
# Routes
# -------------------------------
@app.route('/')
def main():
    return render_template('login.html')


# Thread executor
executor = ThreadPoolExecutor(max_workers=4)

# -------------------------------
# Routes
# -------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # Get form data
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        role = request.form.get('role', 'STUDENT').upper()  # default to STUDENT

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        try:
            # 1️ Insert into sign_up table
            cursor.execute(
                "INSERT INTO sign_up (username, email, password, role) VALUES (%s, %s, %s, %s)",
                (username, email, password, role)
            )
            mysql.connection.commit()
            signup_id = cursor.lastrowid  # ID from sign_up table

            if role == 'PROFESSOR':
                # 2️ Create professor record
                cursor.execute("INSERT INTO professors (user_id) VALUES (%s)", (signup_id,))
                mysql.connection.commit()

            elif role == 'STUDENT':
                # 3️ Create student record
                cursor.execute(
                    "INSERT INTO students (Name, Email, Password, ExamStatus) VALUES (%s, %s, %s, %s)",
                    (username, email, password, "Not Started")
                )
                mysql.connection.commit()
                student_id = cursor.lastrowid  # ID from students table

                # 4️ Assign all existing exams to this student
                cursor.execute("SELECT exam_id FROM exams")
                exams = cursor.fetchall()
                for exam in exams:
                   ''' cursor.execute("""
                        INSERT INTO student_exams (exam_id, student_id, status)
                        VALUES (%s, %s, 'Pending')
                        ON DUPLICATE KEY UPDATE status=status
                    """, (exam['exam_id'], student_id))
                mysql.connection.commit()'''

            flash("Registration successful! Please log in.")
            return redirect(url_for('main'))

        except MySQLdb.IntegrityError as e:
            flash("Username or email already exists!")
            return redirect(url_for('register'))

        except Exception as e:
            flash(f"Registration failed: {str(e)}")
            return redirect(url_for('register'))

        finally:
            cursor.close()

    # GET request
    return render_template('register.html')

@app.route('/login', methods=['POST'])
def login():
    global studentInfo
    username = request.form['username'].strip()
    password = request.form['password']
    role = request.form.get('role', 'STUDENT').strip().upper()

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    if role == 'STUDENT':
        cursor.execute("SELECT * FROM students WHERE Name=%s", (username,))
    else:  # ADMIN or PROFESSOR
        cursor.execute("SELECT * FROM sign_up WHERE username=%s AND UPPER(role)=%s", (username, role))

    account = cursor.fetchone()
    cursor.close()

    if account and account.get('Password' if role == 'STUDENT' else 'password') == password:

        session['username'] = account['Name'] if role == 'STUDENT' else account['username']
        session['user_id'] = account['id']  # or account['id'] depending on table
        studentInfo = {
            "Id": account['id'],
            "Name": account['Name'] if role == 'STUDENT' else account['username'],
            "Email": account.get('Email', account.get('email')),
            "Password": account.get('Password', account.get('password')),
            "Role": role
        }

        if role == 'ADMIN':
            return redirect(url_for('adminStudents'))
        elif role == 'PROFESSOR':
            return redirect(url_for('professorDashboard'))
        else:
            return redirect(url_for('studentsDashboard'))
    else:
        flash("Invalid username, password, or role")
        return redirect(url_for('main'))
    
@app.route('/adminStudents')
def adminStudents():
    if 'username' not in session:
        return redirect(url_for('main'))
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Fetch all users (students and professors) instead of just students
        cur.execute("SELECT id, username, email, role FROM sign_up ORDER BY role, id")
        users = cur.fetchall()
    except Exception as e:
        flash(f"Error fetching users: {str(e)}")
        users = []
    finally:
        cur.close()
    return render_template('AdminStudents.html', students=users)  # Keep template name for consistency


# -------------------------------
# Logout
# -------------------------------
@app.route('/logout')
def logout():
    session.clear()
    global studentInfo
    studentInfo = None
    utils.release_resources()
    return redirect(url_for('main'))

@app.route('/exam', methods=['GET', 'POST'])
def exam():
    if 'username' not in session:
        return redirect(url_for('main'))
    if request.method == 'POST':
        return jsonify({"output": "success"})
    # GET request: start exam
    utils.reset_violations()
    if not utils.cap or not utils.cap.isOpened():
        utils.cap = cv2.VideoCapture(0)
    if not utils.cap.isOpened():
        flash("Error: Webcam not available")
        return redirect(url_for('systemCheckError'))
    try:
        keyboard.hook(utils.shortcut_handler)
        executor.submit(utils.cheat_Detection1)
        executor.submit(utils.cheat_Detection2)
        executor.submit(utils.cheat_Detection3)
        executor.submit(utils.fr.run_recognition)
        return render_template('Exam.html')
    except Exception as e:
        flash(f"Error starting exam: {str(e)}")
        return redirect(url_for('rules'))

    # GET request: fetch questions for this exam
    questions = get_exam_questions(exam_id)  # Implement this: fetch questions from DB
    return render_template('take_exam.html', questions=questions, exam_id=exam_id)


'''@app.route('/availableExams')
def availableExams():
    if 'username' not in session:
        return redirect(url_for('main'))
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM exams ORDER BY scheduled_at")
    exams = cur.fetchall()
    cur.close()
    return render_template("StudentExams.html", exams=exams)
'''
@app.route('/reportProblem', methods=['GET', 'POST'])
def reportProblem():
    if 'username' not in session:
        return redirect(url_for('main'))
    if request.method == 'POST':
        issue = request.form['issue']
        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO feedback (username, issue) VALUES (%s, %s)", (session['username'], issue))
            mysql.connection.commit()
            flash("Feedback submitted!")
        except Exception as e:
            flash(f"Error submitting feedback: {str(e)}")
        finally:
            cur.close()
        return redirect(url_for('rules'))
    return render_template('ReportProblem.html')



# -------------------------------  
# Student / Exam Routes  
# -------------------------------  
@app.route('/rules/<int:exam_id>')
def rules(exam_id):
    # Pass exam_id to the template
    return render_template('ExamRules.html', exam_id=exam_id)

@app.route("/saveStudentImage/<int:exam_id>", methods=["POST"])
def saveStudentImage(exam_id):
    if 'username' not in session:
        return "Unauthorized", 401

    data = request.get_json()
    image_data = data['image']

    import base64
    from pathlib import Path
    header, encoded = image_data.split(",", 1)
    binary_data = base64.b64decode(encoded)

    Path("student_images").mkdir(exist_ok=True)
    filename = f"student_images/{session['username']}_exam{exam_id}.jpg"
    with open(filename, "wb") as f:
        f.write(binary_data)

    return "Image saved"

# -------------------------------
# Video Capture / Face Input
# -------------------------------
def capture_by_frames():
    if not utils.cap or not utils.cap.isOpened():
        utils.cap = cv2.VideoCapture(0)
    if not utils.cap.isOpened():
        print("[ERROR] Could not open webcam")
        return
    detector = cv2.CascadeClassifier('Haarcascades/haarcascade_frontalface_default.xml')
    if detector.empty():
        print("[ERROR] Haar cascade not loaded")
        return
    try:
        while True:
            success, frame = utils.cap.read()
            if not success:
                continue
            faces = detector.detectMultiScale(frame, 1.2, 6)
            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 3)
            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        utils.release_resources()


@app.route('/faceInput/<int:exam_id>', methods=['GET', 'POST'])
def faceInput(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    if request.method == 'POST':
        data = request.get_json()  # get the captured image from frontend
        if not data or 'image' not in data:
            return jsonify({'status': 'error', 'message': 'No image received'}), 400

        image_data = data['image']

        # Remove the base64 header
        import base64, os
        image_data = image_data.split(",")[1]

        try:
            image_bytes = base64.b64decode(image_data)

            # Make sure folder exists
            os.makedirs('static/faces', exist_ok=True)

            # Save image with username and exam_id
            filename = f'static/faces/{session["username"]}_{exam_id}.jpg'
            with open(filename, 'wb') as f:
                f.write(image_bytes)

            return jsonify({'status': 'success'})  # send success back to JS
        except Exception as e:
            print("Error saving image:", e)
            return jsonify({'status': 'error', 'message': 'Failed to save image'}), 500

    # GET request: render the face input page
    import cv2
    if not utils.cap or not utils.cap.isOpened():
        utils.cap = cv2.VideoCapture(0)
    if not utils.cap.isOpened():
        flash("Error: Webcam not available")
        return redirect(url_for('systemCheckError'))

    return render_template('ExamFaceInput.html', exam_id=exam_id)



@app.route('/confirm/<int:exam_id>')
def confirmFaceInput(exam_id):
    # your logic
    return render_template('ExamConfirmFaceInput.html', exam_id=exam_id)

@app.route('/systemCheck/<int:exam_id>')
def systemCheck(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM exams WHERE exam_id=%s", (exam_id,))
    exam = cur.fetchone()
    cur.close()

    if not exam:
        flash("Exam not found!")
        return redirect(url_for('studentsDashboard'))

    return render_template("ExamSystemCheck.html", exam=exam)

@app.route('/systemCheckError/<int:exam_id>')
def systemCheckError(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    # Optional: fetch exam info for display or redirect purposes
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM exams WHERE exam_id=%s", (exam_id,))
    exam = cur.fetchone()
    cur.close()

    if not exam:
        flash("Exam not found!")
        return redirect(url_for('studentsDashboard'))

    # Render error page with a link back to system check or student dashboard
    return render_template("ExamSystemCheckError.html", exam=exam)


@app.route("/studentsDashboard")
def studentsDashboard():
    if 'username' not in session:
        return redirect(url_for('main'))

    username = session['username']
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Get student ID
    cur.execute("SELECT id FROM sign_up WHERE username=%s", (username,))
    student = cur.fetchone()
    if not student:
        cur.close()
        flash("Student not found!")
        return redirect(url_for('main'))
    student_id = student['id']
    print("Student ID:", student_id)  # Debug line

    # Fetch exams assigned to this student
    cur.execute("""
        SELECT e.exam_id, e.title, e.description, e.scheduled_at, se.status
        FROM student_exams se
        JOIN exams e ON se.exam_id = e.exam_id
        WHERE se.student_id = %s
        ORDER BY e.scheduled_at DESC
    """, (student_id,))
    exams = cur.fetchall()
    cur.close()

    return render_template("Students.html", exams=exams)


# Start proctoring threads
def start_proctoring():
    t1 = threading.Thread(target=cheat_Detection1, daemon=True)
    t2 = threading.Thread(target=cheat_Detection2, daemon=True)
    t3 = threading.Thread(target=cheat_Detection3, daemon=True)
    t1.start()
    t2.start()
    t3.start()
    return [t1, t2, t3]


@app.route("/takeExam/<int:exam_id>", methods=['GET', 'POST'])
def takeExam(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    username = session['username']

    # 1️Get student ID
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT id FROM sign_up WHERE username=%s", (username,))
    student = cur.fetchone()
    if not student:
        flash("Student not found!")
        cur.close()
        return redirect(url_for('studentsDashboard'))
    student_id = student['id']

    # Set current student and exam for cheat detection
    utils.set_current_student(student_id)
    utils.set_current_exam(exam_id)


    answers = {}        # initialize to prevent UnboundLocalError
    percentage = 0      # initialize to prevent UnboundLocalError
    status = ""         # initialize to prevent UnboundLocalError

    # 2️ Fetch exam details and check if assigned to student
    cur.execute("""
        SELECT e.exam_id, e.title AS exam_name, e.description, e.scheduled_at, se.status
        FROM exams e
        JOIN student_exams se ON e.exam_id = se.exam_id
        WHERE e.exam_id = %s AND se.student_id = %s
    """, (exam_id, student_id))
    exam = cur.fetchone()
    if not exam:
        flash("Exam not found or not assigned to you!")
        cur.close()
        return redirect(url_for('studentsDashboard'))

    # 3️ Fetch questions for this exam
    cur.execute("""
    SELECT q.id AS question_id, q.question_text, q.option_a, q.option_b, q.option_c, q.option_d, q.correct_option, q.question_type
    FROM exam_questions eq
    JOIN questions q ON eq.question_id = q.id
    WHERE eq.exam_id=%s
""", (exam_id,))
    questions = cur.fetchall()

    cur.close()

    if 'proctor_started' not in session:
      utils.start_proctoring()
      session['proctor_started'] = True


    if request.method == 'POST':
        # 4️ Collect student answers from form
        answers = {}
        for q in questions:
            ans = request.form.get(f"answer_{q['question_id']}")
            answers[q['question_id']] = ans

        # Save student answers to database
        cur = mysql.connection.cursor()
        try:
            for qid, ans in answers.items():
              cur.execute("""
                INSERT INTO student_answers (student_id, exam_id, question_id, answer)
                VALUES (%s, %s, %s, %s)
               """, (student_id, exam_id, qid, ans))
            mysql.connection.commit()
        except Exception as e:
            mysql.connection.rollback()
            flash(f"Error saving student answers: {str(e)}")
        finally:
            cur.close()

        # 5️Calculate score (replace with DB logic if needed)
        score = 0
        for q in questions:
            qid = q['question_id']
            correct = q['correct_option']
            if answers.get(qid) == correct:
                score += 1

        total_questions = len(questions)
        percentage = round((score / total_questions) * 100, 2)
        status = "Passed" if percentage >= 40 else "Failed"

            


        # 6️ Save results in DB
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        try:
            violations_count = len(utils.violations)
            trust_score = max(0, 100 - (violations_count * 10))
            cur.execute("""
            INSERT INTO results (student_id, exam_id, score, status, violations, trust_score)
            VALUES (%s, %s, %s, %s, %s, %s)
           """, (student_id, exam_id, percentage, status, violations_count, trust_score))

            mysql.connection.commit()
        except Exception as e:
            mysql.connection.rollback()
            flash(f"Error saving result: {str(e)}")
        finally:
            cur.close()

        # Stop camera and mark exam completed
        utils.release_resources()
        mark_exam_completed(student_id, exam_id)

        # Flash message with score
        flash(f"Exam submitted! You scored {percentage}%. Status: {status}")

        # Redirect to student results page
        return redirect(url_for('studentResults'))
   
    # Add this line to handle GET requests
    return render_template("take_exam.html", exam=exam, questions=questions)




def mark_exam_completed(student_id, exam_id):
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # 1️ Update student_exams status
        cur.execute("""
            UPDATE student_exams
            SET status='Completed'
            WHERE student_id=%s AND exam_id=%s
        """, (student_id, exam_id))

        # 2️ Save violations in results (if already inserted)
        violations_count = len(utils.violations)
        trust_score = max(0, 100 - (violations_count * 10))
        cur.execute("""
            UPDATE results
            SET violations=%s
            WHERE student_id=%s AND exam_id=%s
        """, (violations_count, student_id, exam_id))

        mysql.connection.commit()
    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error updating exam status or violations: {str(e)}")
    finally:
        cur.close()


@app.route("/myResults/<int:exam_id>")
def myResults(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    username = session['username']
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Get student ID
    cur.execute("SELECT id FROM sign_up WHERE username=%s", (username,))
    student = cur.fetchone()
    if not student:
        flash("Student not found!")
        cur.close()
        return redirect(url_for('Students'))  # your dashboard

    student_id = student['id']

    # Fetch result for this exam
    cur.execute("""
        SELECT r.*, e.title AS exam_name
        FROM results r
        JOIN exams e ON r.exam_id = e.exam_id
        WHERE r.student_id = %s AND r.exam_id = %s
    """, (student_id, exam_id))
    result = cur.fetchone()
    cur.close()

    if not result:
        flash("Result not found!")
        return redirect(url_for('Students'))

    return render_template("StudentResults.html", results=[result])


@app.route("/studentResults")
def studentResults():
    if 'username' not in session:
        return redirect(url_for('main'))

    username = session['username']
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Get student ID
    cur.execute("SELECT id FROM sign_up WHERE username=%s", (username,))
    student = cur.fetchone()
    if not student:
        flash("Student not found!")
        cur.close()
        return redirect(url_for('Students'))
    student_id = student['id']

    # Fetch results only for this student
    cur.execute("""
        SELECT r.*, e.title AS exam_name
        FROM results r
        JOIN exams e ON r.exam_id = e.exam_id
        WHERE r.student_id=%s
        ORDER BY r.created_at DESC
    """, (student_id,))
    results = cur.fetchall()
    cur.close()

    return render_template("StudentResults.html", results=results)



# -------------------------------
# Professor Routes (replace existing professor routes with this block)
# -------------------------------
@app.route('/professorDashboard')
def professorDashboard():
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Get professor_id
        cur.execute("""
            SELECT p.professor_id
            FROM professors p
            JOIN sign_up s ON p.user_id = s.id
            WHERE s.username = %s
        """, (session['username'],))
        professor = cur.fetchone()
        if not professor:
            flash("Professor record not found.")
            return redirect(url_for('main'))
        professor_id = professor['professor_id']

        # FETCH EXAMS DIRECTLY BY PROFESSOR_ID
        # FETCH EXAMS DIRECTLY BY PROFESSOR_ID
        cur.execute("""
           SELECT * 
           FROM exams 
           WHERE professor_id = %s 
           ORDER BY scheduled_at DESC
        """, (professor_id,))
        exams = cur.fetchall() or []




        # Add question count
        for e in exams:
            e['id'] = e['exam_id']  # map for template
            cur.execute("""
        SELECT COUNT(*) AS count
        FROM exam_questions
        WHERE exam_id=%s
    """, (e['exam_id'],))
            result = cur.fetchone()
            e['questions_count'] = result['count'] if result else 0

        # Fetch all questions (optional)
        cur.execute("""
        SELECT *
        FROM questions
        WHERE professor_id = %s
        ORDER BY id DESC
        """, (professor_id,))
        questions = cur.fetchall() or []


        # Fetch students
        cur.execute("SELECT id, username FROM sign_up WHERE UPPER(role)='STUDENT'")
        students = cur.fetchall() or []

    except Exception as exc:
        flash(f"Error loading dashboard: {str(exc)}")
        exams = []
        questions = []
        students = []
    finally:
        cur.close()

    return render_template('Professor.html', exams=exams, questions=questions, students=students)


@app.route('/addQuestion', methods=['POST'])
def addQuestion():
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # 1. Get professor_id based on logged-in username
        cur.execute("""
            SELECT p.professor_id
            FROM professors p
            JOIN sign_up s ON p.user_id = s.id
            WHERE s.username = %s
        """, (session['username'],))
        professor = cur.fetchone()
        if not professor:
            flash("⚠ No professor record found for this user.")
            return redirect(url_for('professorDashboard'))

        professor_id = professor['professor_id']

        # 2. Collect form data
        question_text = request.form.get('question_text', '').strip()
        option_a = request.form.get('option_a', '').strip()
        option_b = request.form.get('option_b', '').strip()
        option_c = request.form.get('option_c', '').strip()
        option_d = request.form.get('option_d', '').strip()
        correct_option = request.form.get('correct_option', '').strip()
        question_type = request.form.get('question_type', 'MCQ')

        # 3. Insert into questions
       # Always insert into Question Bank
        cur.execute("""
INSERT INTO questions
(professor_id, question_text, option_a, option_b, option_c, option_d, correct_option)
VALUES (%s, %s, %s, %s, %s, %s, %s)
""", (professor_id, question_text, option_a, option_b, option_c, option_d, correct_option))



        mysql.connection.commit()
        flash(" Question added successfully!")

    except Exception as e:
        mysql.connection.rollback()
        flash(f" Error adding question: {str(e)}")
        print("DEBUG ERROR ADD QUESTION:", str(e))
    finally:
        cur.close()

    return redirect(url_for('professorDashboard'))



@app.route('/generateQuestions', methods=['POST'])
def generateQuestions():
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor()
    try:
        topic = request.form.get('topic', 'General')
        num_questions = int(request.form.get('num_questions', 3))
        exam_id = request.form.get('exam_id') or None

        # get professor_id
        cur.execute("""
            SELECT p.professor_id FROM professors p
            JOIN sign_up s ON p.user_id = s.id
            WHERE s.username = %s
        """, (session['username'],))
        professor = cur.fetchone()
        if not professor:
            flash("Professor record not found.")
            return redirect(url_for('professorDashboard'))
        professor_id = professor['professor_id']

        # Placeholder "AI" generation
        generated = []
        for i in range(num_questions):
            text = f"Auto-generated question ({topic}) #{i+1}"
            generated.append((professor_id, exam_id, text, 'MCQ', 'Opt A', 'Opt B', 'Opt C', 'Opt D', 'A'))

        cur.executemany("""
            INSERT INTO questions
            (professor_id, exam_id, question_text, question_type, option_a, option_b, option_c, option_d, correct_option)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, generated)

        mysql.connection.commit()
        flash(f"{num_questions} sample questions generated.")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error generating questions: {str(e)}")
    finally:
        cur.close()

    return redirect(url_for('professorDashboard'))


@app.route('/deleteQuestion/<int:id>', methods=['GET', 'POST'])
def deleteQuestion(id):
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor()
    try:
        cur.execute("DELETE FROM questions WHERE id = %s", (id,))
        mysql.connection.commit()
        flash("🗑 Question deleted successfully!")
    except Exception as e:
        mysql.connection.rollback()
        flash(f" Error deleting question: {str(e)}")
    finally:
        cur.close()

    return redirect(url_for('professorDashboard'))


@app.route('/editQuestion/<int:question_id>', methods=['GET', 'POST'])
def editQuestion(question_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # GET: show the edit page
        if request.method == 'GET':
            cur.execute("SELECT * FROM questions WHERE id=%s", (question_id,))
            question = cur.fetchone()
            if not question:
                flash("Question not found.")
                return redirect(url_for('professorDashboard'))
            return render_template('EditQuestion.html', question=question)

        # POST: update
        question_type = request.form.get('question_type', 'MCQ')
        question_text = request.form.get('question_text', '').strip()

        if question_type == 'MCQ':
            option_a = request.form.get('option_a', '').strip()
            option_b = request.form.get('option_b', '').strip()
            option_c = request.form.get('option_c', '').strip()
            option_d = request.form.get('option_d', '').strip()
            correct_option = request.form.get('correct_option', '').upper().strip()

            cur.execute("""
                UPDATE questions
                SET question_text=%s, question_type=%s, option_a=%s, option_b=%s, option_c=%s, option_d=%s, correct_option=%s
                WHERE id=%s
            """, (question_text, 'MCQ', option_a, option_b, option_c, option_d, correct_option, question_id))
        else:
            cur.execute("UPDATE questions SET question_text=%s, question_type=%s WHERE id=%s",
                        (question_text, question_type, question_id))

        mysql.connection.commit()
        flash("Question updated successfully!")
    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error updating question: {str(e)}")
    finally:
        cur.close()

    return redirect(url_for('professorDashboard'))

@app.route('/createExam', methods=['POST'])
def createExam():
    if 'username' not in session:
        flash("Please login first.")
        return redirect(url_for('main'))

    # Get form data
    title = request.form.get('exam_name', '').strip()
    description = request.form.get('description', '').strip()
    scheduled_at = request.form.get('scheduled_at') or None
    if scheduled_at:
        scheduled_at = scheduled_at.replace('T', ' ')  # Format for MySQL

    selected_questions = request.form.getlist('questions[]')  # selected from Question Bank

    if not title:
        flash("Exam name is required.")
        return redirect(url_for('professorDashboard'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Get professor_id
        cur.execute("""
            SELECT p.professor_id FROM professors p
            JOIN sign_up s ON p.user_id = s.id
            WHERE s.username = %s
        """, (session['username'],))
        professor = cur.fetchone()
        if not professor:
            flash("Professor not found in database!")
            return redirect(url_for('professorDashboard'))
        professor_id = professor['professor_id']

        # Insert the exam
        cur.execute("""
            INSERT INTO exams (title, description, professor_id, scheduled_at)
            VALUES (%s, %s, %s, %s)
        """, (title, description, professor_id, scheduled_at))
        exam_id = cur.lastrowid

        if not exam_id:
            flash("Failed to create exam. No ID returned.")
            return redirect(url_for('professorDashboard'))

        # Assign selected questions safely
        for q_id in selected_questions:
            # Make sure q_id exists
            cur.execute("SELECT id FROM questions WHERE id=%s", (q_id,))
            if cur.fetchone():
                try:
                    cur.execute("INSERT INTO exam_questions (exam_id, question_id) VALUES (%s, %s)", (exam_id, q_id))
                except MySQLdb.IntegrityError:
                    pass  # ignore duplicates
            else:
                print(f"Warning: Question ID {q_id} does not exist.")

        # Assign exam to all students
        # FIXED: assign to all students from sign_up table
        cur.execute("""
   INSERT INTO student_exams (exam_id, student_id, status)
   SELECT %s, id, 'Pending'
   FROM sign_up
   WHERE UPPER(role)='STUDENT'
   ON DUPLICATE KEY UPDATE status=status
""", (exam_id,))



        mysql.connection.commit()
        flash(f"Exam '{title}' created successfully with ID {exam_id}!")

    except Exception as e:
        mysql.connection.rollback()
        flash(f"Error creating exam: {str(e)}")
        print("DEBUG ERROR:", str(e))

    finally:
        cur.close()

    return redirect(url_for('professorDashboard'))




@app.route('/viewResults', endpoint='viewResults')
def viewAllResults():
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Get professor_id
        cur.execute("""
            SELECT p.professor_id FROM professors p
            JOIN sign_up s ON p.user_id = s.id
            WHERE s.username = %s
        """, (session['username'],))
        professor = cur.fetchone()
        if not professor:
            flash("Professor not found.")
            return redirect(url_for('professorDashboard'))
        professor_id = professor['professor_id']

        # Fetch results for exams of this professor
        cur.execute("""
            SELECT r.*, s.username AS student_name, e.title AS exam_title
            FROM results r
            JOIN sign_up s ON r.student_id = s.id
            JOIN exams e ON r.exam_id = e.exam_id
            WHERE e.professor_id = %s
            ORDER BY r.id DESC
        """, (professor_id,))
        results = cur.fetchall() or []

        # Add image paths for students (assuming you saved images in static/faces/)
        for r in results:
                # Set default values if DB field is missing or None
            r['trust_score'] = r.get('trust_score', 0)
            r['violations'] = r.get('violations', 0)
            r['image_path'] = f'faces/{r["student_name"]}_{r["exam_id"]}.jpg'

    except Exception as e:
        flash(f"Error fetching results: {str(e)}")
        results = []
    finally:
        cur.close()

    return render_template('Results.html', results=results)


@app.route('/professor/result/<int:student_id>/<int:exam_id>')
def viewResultDetails(student_id, exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Fetch student info
        cur.execute("SELECT id, username, email FROM sign_up WHERE id=%s", (student_id,))
        student = cur.fetchone()
        if not student:
            flash("Student not found.")
            return redirect(url_for('viewResults'))
        student['image_path'] = f'faces/{student["username"]}_{exam_id}.jpg'

        # Fetch exam info
        cur.execute("SELECT exam_id, title AS exam_title, scheduled_at AS date FROM exams WHERE exam_id=%s", (exam_id,))
        exam = cur.fetchone()
        if not exam:
            flash("Exam not found.")
            return redirect(url_for('viewResults'))

        # Fetch result info
        cur.execute("""
            SELECT score, trust_score, violations
            FROM results
            WHERE student_id=%s AND exam_id=%s
        """, (student_id, exam_id))
        result = cur.fetchone()
        if not result:
            result = {'score': 0, 'trust_score': 100, 'violations': 0}

        # Fetch all questions (even unanswered)
        cur.execute("""
            SELECT q.question_text,
             COALESCE(sa.answer, '-') AS student_answer,
           q.correct_option AS correct_answer
           FROM questions q
           LEFT JOIN student_answers sa
           ON sa.question_id = q.id 
           AND sa.student_id = %s 
           AND sa.exam_id = q.exam_id
    WHERE q.exam_id = %s
    ORDER BY q.id
        """, (student_id, exam_id))
        questions = cur.fetchall()


        # Fetch violations
        cur.execute("""
            SELECT type, timestamp AS time, details AS notes
            FROM violations
            WHERE student_id=%s AND exam_id=%s
        """, (student_id, exam_id))
        violation_details = cur.fetchall()

        # Optional: format violation time for display
        for v in violation_details:
            if isinstance(v['time'], (str, type(None))):
                continue
            v['time'] = v['time'].strftime('%H:%M:%S')

    except Exception as e:
        flash(f"Error fetching result details: {str(e)}")
        student = exam = result = {}
        questions = violation_details = []
    finally:
        cur.close()

    return render_template(
        'ResultDetails.html',
        student=student,
        exam=exam,
        result=result,
        questions=questions,
        violation_details=violation_details
    )


from datetime import datetime
import json 

@app.route('/viewViolations')
def viewViolations():
    # Shows the violations log for the professor (reads file or utils)
    try:
        with open("violations.json", "r") as f:
            violations = json.load(f)
    except Exception:
        violations = getattr(utils, 'violations', []) or []

    current_time = datetime.utcnow()  # Pass current UTC time
    
# Convert string timestamps to datetime objects
    for v in violations:
      if "timestamp" in v:
        v["timestamp_parsed"] = datetime.fromisoformat(v["timestamp"])

    return render_template("violations.html", violations=violations, current_time=current_time)


@app.route('/professor/exam/<int:exam_id>/questions')
def view_exam_questions(exam_id):
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # 1️ Get exam details
        cur.execute("SELECT * FROM exams WHERE exam_id = %s", (exam_id,))
        exam = cur.fetchone()
        if not exam:
            flash("Exam not found.")
            return redirect(url_for('professorDashboard'))

        # 2️ Get all questions linked to this exam
        cur.execute("""
    SELECT q.*
    FROM questions q
    JOIN exam_questions eq ON q.id = eq.question_id
    WHERE eq.exam_id = %s
    ORDER BY q.id ASC
""", (exam_id,))
        questions = cur.fetchall() or []

        # 3️ Optional: count questions
        question_count = len(questions)

    except Exception as e:
        flash(f"Error fetching questions: {str(e)}")
        exam = None
        questions = []
        question_count = 0
    finally:
        cur.close()

    return render_template(
        'view_exam_questions.html',
        exam=exam,
        questions=questions,
        question_count=question_count
    )

# app.py (or professor routes section)
@app.route('/professor/exam/<int:exam_id>/delete', methods=['POST'])
def delete_exam(exam_id):
    cursor = mysql.connection.cursor()
    cursor.execute("DELETE FROM exams WHERE exam_id = %s", (exam_id,))
    mysql.connection.commit()
    cursor.close()
    flash("Exam deleted successfully!", "success")
    return redirect(url_for('professorDashboard'))

@app.route('/students')
def students():
    if 'username' not in session:
        return redirect(url_for('main'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # Get logged in student_id
        cur.execute("SELECT id FROM sign_up WHERE username = %s", (session['username'],))
        student = cur.fetchone()
        if not student:
            flash("Student not found.")
            return redirect(url_for('main'))

        student_id = student['id']

        # Fetch exams assigned to this student
        cur.execute("""
            SELECT e.exam_id, e.title, e.description, e.scheduled_at,
                   COALESCE(se.status, 'Pending') AS status
            FROM exams e
            LEFT JOIN student_exams se 
                ON e.exam_id = se.exam_id AND se.student_id = %s
            ORDER BY e.scheduled_at DESC
        """, (student_id,))

        # Correct indentation: must be inside try
        exams = cur.fetchall() or []

    except Exception as e:
        flash(f"Error loading student dashboard: {str(e)}")
        exams = []
    finally:
        cur.close()

    return render_template('Students.html', exams=exams)



# -------------------------------
# Cleanup
# -------------------------------
@app.teardown_appcontext
def cleanup(exception):
    utils.release_resources()

# -------------------------------
# Run App
# -------------------------------
if __name__ == '__main__':
    app.run(debug=True)