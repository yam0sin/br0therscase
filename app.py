from flask import Flask, request, jsonify, session, render_template, redirect, url_for
# from flask_sqlalchemy import SQLAlchemy
# from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import random
import os

import requests

BASE_URL = 'https://script.google.com/macros/s/AKfycbwoI6PHGp_UqOMbnUrO3kiyBVUJi4Ur8yG5jktU60LWcXF1IsNS8tkxelWLXCl14TwXlg/exec'

SHEET_SKINS_URL = BASE_URL + '?type=skins'
SHEET_USER_URL = BASE_URL + '?type=users'
SHEET_HISTORY_URL = BASE_URL + '?type=history'

def load_users_from_sheet():
    try:
        response = requests.get(SHEET_USER_URL)
        data = response.json()
        return [
            {
                'username': row['Пользователь'],
                'password': row['Пароль'],
                'balance': int(row['Баланс'])
            }
            for row in data if 'Пользователь' in row
        ]
    except Exception as e:
        print("[ERROR] Ошибка при загрузке пользователей:", e)
        return []

def update_user_balance(username, new_balance):
    try:
        requests.post(SHEET_USER_URL, json={
            "action": "update_balance",
            "username": username,
            "new_balance": new_balance
        })
    except Exception as e:
        print("[WARN] Не удалось обновить баланс:", e)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 🔄 Функция отправки в Google Таблицу
def send_to_google_sheet(user, dropped_skin):
    print("DROPPED SKIN:", dropped_skin)
    try:
        requests.post(
            SHEET_SKINS_URL,
            json={
                "username": user['username'],
                "skin": dropped_skin['Скин'],
                "rarity": dropped_skin['Редкость'],
                "quality": dropped_skin.get('Качество', ''),
                "image_url": dropped_skin.get('Фотография', '')  # ← оставляем 'Фотография', если именно так в скинах
            }
        )
    except Exception as e:
        print("[WARN] Не удалось записать в таблицу:", e)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/history')
def history():
    username = session.get('user_id')
    if not username:
        return redirect(url_for('home'))

    try:
        response = requests.get(SHEET_HISTORY_URL)
        sheet_data = response.json()
    except Exception as e:
        print("[ERROR] Не удалось загрузить историю:", e)
        sheet_data = []

    user_drops = []
    for row in sheet_data:
        if row.get('Пользователь') == username:
            user_drops.append({
                'skin': row.get('Скин'),
                'rarity': row.get('Редкость'),
                'quality': row.get('Качество'),
                'image_url': row.get('Фотография', ''),
                'timestamp': row.get('Дата', ''),
                'status': row.get('Статус', '')
            })


    return render_template('history.html', user={'username': username}, drops=user_drops[::-1])

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
    sheet_users = load_users_from_sheet()
    user = next((u for u in sheet_users if u['username'] == data['username'] and u['password'] == data['password']), None)
    if user:
        session['user_id'] = user['username']  # теперь в сессии имя
        return jsonify({'message': 'Login successful'})
    return jsonify({'message': 'Invalid credentials'}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'message': 'Logged out'})

@app.route('/me')
def me():
    username = session.get('user_id')
    if not username:
        return jsonify({'logged_in': False})
    users = load_users_from_sheet()
    user = next((u for u in users if u['username'] == username), None)
    if user:
        return jsonify({'logged_in': True, 'username': user['username'], 'balance': user['balance']})
    return jsonify({'logged_in': False})

import requests

@app.route('/open_case', methods=['POST'])
def open_case():
    username = session.get('user_id')
    sheet_users = load_users_from_sheet()
    user = next((u for u in sheet_users if u['username'] == username), None)
    if not user:
        return jsonify({'message': 'Unauthorized'}), 401

    if user['balance'] < 10 and user['username'] != 'admin':
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
    url = SHEET_SKINS_URL
    try:
        response = requests.get(url)
        sheet_data = response.json()
    except Exception as e:
        return jsonify({'message': 'Ошибка загрузки скинов из таблицы', 'error': str(e)}), 500

    # Группировка по редкости
    rarity_buckets = {r: [] for r in rarity_weights}
    for row in sheet_data:
        name = row.get('Скин', '').strip()
        rarity = row.get('Редкость', '').strip()
        image = row.get('Фотография', '').strip()

        if name and rarity and image and rarity in rarity_buckets:
            rarity_buckets[rarity].append(row)

    filtered_weights = {r: w for r, w in rarity_weights.items() if rarity_buckets[r]}
    if not filtered_weights:
        return jsonify({'message': 'Нет доступных скинов по редкостям'}), 400

    # Выбираем скин
    chosen_rarity = random.choices(list(filtered_weights.keys()), weights=filtered_weights.values())[0]
    dropped_skin = random.choice(rarity_buckets[chosen_rarity])

    # Если не админ — уменьшаем и сохраняем
    if user['username'] != 'admin':
        new_balance = user['balance'] - 10
        update_user_balance(user['username'], new_balance)

#        db_skin = Skin.query.filter_by(name=dropped_skin['name']).first()
#       if not db_skin:
#           db_skin = Skin(
#               name=dropped_skin['name'],
#               rarity=dropped_skin['rarity'],
#               quality=dropped_skin.get('quality', ''),
#               image_url=dropped_skin['image_url'],
#               quantity=1,
#               available=False
#           )
#           db.session.add(db_skin)
#           db.session.flush()
#       else:
#           db_skin.quantity -= 1
#           if db_skin.quantity <= 0:
#               db_skin.available = False
#        drop = Drop(user_id=user.id, skin_id=db_skin.id)
#        db.session.add(drop)

        # Отправляем в Google Таблицу
        send_to_google_sheet(user, dropped_skin)

    # Отдаём скин на фронт
    return jsonify({
        'message': 'Skin dropped!',
        'skin': {
            'name': dropped_skin['Скин'],
            'rarity': dropped_skin['Редкость'],
            'image_url': dropped_skin['Фотография'],
         'quality': dropped_skin.get('Качество', '')
        },
        'balance': new_balance if user['username'] != 'admin' else user['balance']
    })








import requests

@app.route('/animation_skins')
def animation_skins():
    url = SHEET_SKINS_URL
    try:
        response = requests.get(url)
        data = response.json()
    except Exception as e:
        return jsonify({'message': 'Ошибка загрузки скинов', 'error': str(e)}), 500

    skins = []
    for row in data:
        if 'Скин' in row and 'Редкость' in row and 'Фотография' in row:
            skins.append({
                'name': row['Скин'],
                'rarity': row['Редкость'],
                'image_url': row['Фотография'],
                'quality': row.get('Качество', '')
            })


    return jsonify(skins)


@app.route('/admin/user/<int:user_id>/history')
def admin_user_history(user_id):
    user = User.query.get_or_404(user_id)
    drops = Drop.query.filter_by(user_id=user.id).order_by(Drop.timestamp.desc()).all()
    return render_template('admin_user_history.html', user=user, drops=drops)

@app.route('/sell_skin', methods=['POST'])
def sell_skin():
    username = session.get('user_id')
    if not username:
        return jsonify({'message': 'Unauthorized'}), 401

    data = request.get_json()
    skin_name = data.get('skin')
    quality = data.get('quality')

    try:
        # 1. Загружаем данные
        skins = requests.get(SHEET_SKINS_URL).json()
        users = load_users_from_sheet()
        history = requests.get(SHEET_HISTORY_URL).json()
    except Exception as e:
        return jsonify({'message': 'Ошибка загрузки данных', 'error': str(e)}), 500

    user = next((u for u in users if u['username'] == username), None)
    if not user:
        return jsonify({'message': 'Пользователь не найден'}), 404

    # 2. Ищем цену
    matched_skin = next((s for s in skins if s['Скин'] == skin_name and s['Качество'] == quality), None)
    if not matched_skin or not matched_skin.get('Цена'):
        return jsonify({'message': 'Скин не найден или нет цены'}), 404

    try:
        price = float(str(matched_skin['Цена']).replace(',', '.'))
    except:
        return jsonify({'message': 'Неверный формат цены'}), 400

    # 3. Проверка на проданный скин
    for i, row in enumerate(history):
        if (
            row.get('Пользователь') == username and
            row.get('Скин') == skin_name and
            row.get('Качество') == quality and
            (row.get('Статус', '') != 'Продан')
        ):
            # 4. Обновляем баланс
            new_balance = user['balance'] + price
            update_user_balance(username, new_balance)

            # 5. Увеличиваем количество скина
            print("[POST] return_skin", skin_name, quality)
            try:
                requests.post(SHEET_SKINS_URL, json={
                    "action": "return_skin",
                    "skin": skin_name,
                    "quality": quality
                })
            except Exception as e:
                print("[WARN] Ошибка при возврате скина:", e)

            # 6. Отмечаем скин как проданный в истории
            print("[POST] mark_sold", username, skin_name, quality, row.get('Дата', ''))
            try:
                requests.post(SHEET_HISTORY_URL, json={
                "action": "mark_sold",
                "username": username,
                "skin": skin_name,
                "quality": quality,
                "timestamp": row.get('Дата', '')
                })
            except Exception as e:
                print("[WARN] Ошибка при пометке как продано:", e)


            return jsonify({'message': f'Скин продан за {price:.2f}!'})

    return jsonify({'message': 'Скин не найден или уже продан'}), 400

if __name__ == '__main__':
    app.run(debug=True)