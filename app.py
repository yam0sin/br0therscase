from flask import Flask, request, jsonify, session, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    balance = db.Column(db.Integer, default=100)

class Skin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rarity = db.Column(db.String(20), nullable=False)
    quality = db.Column(db.String(50), nullable=True)
    quantity = db.Column(db.Integer, default=1)
    image_url = db.Column(db.String(255), nullable=True)
    available = db.Column(db.Boolean, default=True)

class Drop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    skin_id = db.Column(db.Integer, db.ForeignKey('skin.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    issued = db.Column(db.Boolean, default=False)  # <- вот это добавь

    user = db.relationship('User', backref='drops')
    skin = db.relationship('Skin')

# 🔄 Функция отправки в Google Таблицу
def send_to_google_sheet(user, dropped_skin):
    """Отправляет информацию о выпадении в Google Таблицу"""
    try:
        requests.post(
            'https://script.google.com/macros/s/AKfycbw4a-LVhk-Q3ZcjigsiUvSfhSNonw4BeXHQaJbBDvKL-aPAu-CbaW7ncfWRbJH65KBr_A/exec',
            json={
                "username": user.username,
                "skin": dropped_skin['name'],
                "rarity": dropped_skin['rarity'],
                "quality": dropped_skin.get('quality', '')
            }
        )
    except Exception as e:
        print("[WARN] Не удалось записать в таблицу:", e)

@app.route('/')
def home():
    skins = Skin.query.filter_by(available=True).all()
    return render_template('index.html', skins=skins)

@app.route('/history')
def history():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('home'))
    user = User.query.get(user_id)
    drops = Drop.query.filter_by(user_id=user.id).order_by(Drop.timestamp.desc()).all()
    return render_template('history.html', user=user, drops=drops)

@app.route('/admin/users', methods=['GET', 'POST'])
def admin_users():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('home'))

    user = User.query.get(user_id)
    if user.username != 'admin':
        return redirect(url_for('home'))

    message = ''
    if request.method == 'POST':
        if 'add_user' in request.form:
            new_user = User(
                username=request.form['username'],
                password_hash=generate_password_hash(request.form['password']),
                balance=int(request.form['balance'])
            )
            db.session.add(new_user)
            db.session.commit()
            message = f"Пользователь {new_user.username} добавлен"
        elif 'update_balance' in request.form:
            target_user = User.query.get(int(request.form['user_id']))
            target_user.balance = int(request.form['new_balance'])
            db.session.commit()
            message = f"Баланс обновлён для {target_user.username}"
        elif 'mark_issued_id' in request.form:
            drop_id = int(request.form['mark_issued_id'])
            drop = Drop.query.get(drop_id)
            if drop:
                drop.issued = True
                db.session.commit()
                message = f"Скин для {drop.user.username} отмечен как выдан"

    users = User.query.all()
    all_drops = Drop.query.join(User).filter(User.username.notin_(['admin'])).order_by(Drop.timestamp.desc()).all()

    return render_template(
        'admin_users.html',
        users=users,
        all_drops=all_drops,
        message=message
    )


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password_hash, data['password']):
        session['user_id'] = user.id
        return jsonify({'message': 'Login successful'})
    return jsonify({'message': 'Invalid credentials'}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'message': 'Logged out'})

@app.route('/me')
def me():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'logged_in': False})
    user = User.query.get(user_id)
    return jsonify({'logged_in': True, 'username': user.username, 'balance': user.balance})

import requests

@app.route('/open_case', methods=['POST'])
def open_case():
    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'message': 'Unauthorized'}), 401
    if user.balance < 10 and user.username != 'admin':
        return jsonify({'message': 'Insufficient balance'}), 400

    # Редкости и шансы
    rarity_weights = {
        'Ширпотреб': 76,
        'Промышленное': 18,
        'Армейское': 4,
        'Запрещённое': 0.98,
        'Засекреченное': 0.21,
        'Тайное': 0.05
    }

    # Получаем скины из Google Таблицы
    url = 'https://script.google.com/macros/s/AKfycbyu09I8nWtNgEw7QOwHPG9pwlSZa_GVXm6od8i2DfNefShjMYl-E0Y1qZXzr1oCVLWiaQ/exec'
    try:
        response = requests.get(url)
        sheet_data = response.json()
    except Exception as e:
        return jsonify({'message': 'Ошибка загрузки скинов из таблицы', 'error': str(e)}), 500

    # Группировка по редкости
    rarity_buckets = {r: [] for r in rarity_weights}
    for row in sheet_data:
        name = row.get('name', '').strip()
        rarity = row.get('rarity', '').strip()
        image = row.get('image_url', '').strip()
        if name and rarity and image and rarity in rarity_buckets:
            rarity_buckets[rarity].append(row)

    filtered_weights = {r: w for r, w in rarity_weights.items() if rarity_buckets[r]}
    if not filtered_weights:
        return jsonify({'message': 'Нет доступных скинов по редкостям'}), 400

    # Выбираем скин
    chosen_rarity = random.choices(list(filtered_weights.keys()), weights=filtered_weights.values())[0]
    dropped_skin = random.choice(rarity_buckets[chosen_rarity])

    # Если не админ — уменьшаем и сохраняем
    if user.username != 'admin':
        user.balance -= 10

        db_skin = Skin.query.filter_by(name=dropped_skin['name']).first()
        if not db_skin:
            db_skin = Skin(
                name=dropped_skin['name'],
                rarity=dropped_skin['rarity'],
                quality=dropped_skin.get('quality', ''),
                image_url=dropped_skin['image_url'],
                quantity=1,
                available=False
            )
            db.session.add(db_skin)
            db.session.flush()
        else:
            db_skin.quantity -= 1
            if db_skin.quantity <= 0:
                db_skin.available = False

        drop = Drop(user_id=user.id, skin_id=db_skin.id)
        db.session.add(drop)

        # Отправляем в Google Таблицу
        send_to_google_sheet(user, dropped_skin)

    # Сохраняем изменения
    db.session.commit()

    # Отдаём скин на фронт
    return jsonify({
        'message': 'Skin dropped!',
        'skin': {
            'name': dropped_skin['name'],
            'rarity': dropped_skin['rarity'],
            'image_url': dropped_skin['image_url'],
            'quality': dropped_skin.get('quality', '')
        },
        'balance': user.balance
    })






import requests

@app.route('/animation_skins')
def animation_skins():
    url = 'https://script.google.com/macros/s/AKfycbyu09I8nWtNgEw7QOwHPG9pwlSZa_GVXm6od8i2DfNefShjMYl-E0Y1qZXzr1oCVLWiaQ/exec'
    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        return jsonify({'message': 'Ошибка загрузки скинов', 'error': str(e)}), 500

    skins = []
    for row in data:
        if 'name' in row and 'rarity' in row and 'image_url' in row:
            skins.append({
                'name': row['name'],
                'rarity': row['rarity'],
                'image_url': row['image_url'],
                'quality': row.get('quality', '')
            })


    return jsonify(skins)


@app.route('/admin/user/<int:user_id>/history')
def admin_user_history(user_id):
    user = User.query.get_or_404(user_id)
    drops = Drop.query.filter_by(user_id=user.id).order_by(Drop.timestamp.desc()).all()
    return render_template('admin_user_history.html', user=user, drops=drops)

if __name__ == '__main__':
    with app.app_context():
        from flask_migrate import upgrade
        upgrade()
    app.run(debug=True)