# 🏢 ОЦІНКА24 — Telegram Bot

Бот для збору документів, ідентифікації клієнтів,
геолокації об'єктів та запису на відеоогляд.

---

## ⚙️ Встановлення (покрокова інструкція)

### Крок 1 — Отримати токен бота

1. Написати [@BotFather](https://t.me/BotFather) у Telegram
2. Команда: `/newbot`
3. Назва бота: `ОЦІНКА24`
4. Username бота: `otsinka24_bot` (або будь-який вільний)
5. Скопіювати токен вигляду: `1234567890:ABCDEFabc...`

---

### Крок 2 — Дізнатися свій Telegram ID (для адміна)

Написати [@userinfobot](https://t.me/userinfobot) — він поверне ваш числовий ID.

---

### Крок 3 — Встановити Python та залежності

```bash
# Переконатись, що Python 3.10+ встановлений
python3 --version

# Створити та активувати віртуальне середовище
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# або: venv\Scripts\activate    # Windows

# Встановити бібліотеки
pip install -r requirements.txt
```

---

### Крок 4 — Налаштувати змінні середовища

```bash
cp .env.example .env
nano .env   # або відкрийте в будь-якому редакторі
```

Заповнити:
```
BOT_TOKEN=ВАШ_ТОКЕН_ВІД_BOTFATHER
ADMIN_IDS=ВАШ_TELEGRAM_ID
```

---

### Крок 5 — Запустити бота

```bash
# Вручну (для тесту):
python bot.py

# Або через systemd (для сервера, щоб бот працював постійно):
# Дивіться секцію "Деплой на сервер" нижче
```

---

## 🤖 Функції бота

| Кнопка              | Дія                                               |
|---------------------|---------------------------------------------------|
| 🚀 Повна процедура  | Всі 4 кроки послідовно (можна пропускати)         |
| 🪪 Ідентифікація    | Фото паспорта + селфі з паспортом                 |
| 📄 Документи        | Вибір типу + надсилання (фото або PDF)            |
| 📍 Геолокація       | GPS-координати або текстова адреса                |
| 🎥 Відеоогляд       | Запис на зручний час (Пн–Пт)                      |
| ℹ️ Про компанію     | Інформація про ОЦІНКА24                           |
| 📞 Контакти         | Телефон, email, графік роботи                     |

---

## 📬 Що отримує адміністратор

При кожній дії клієнта адмін отримує:
- **Ідентифікація** → фото паспорта + селфі + дані клієнта
- **Документи** → кожен файл окремо з підписом (тип + ПІБ + ID)
- **Геолокація** → pin на карті + посилання Google Maps
- **Відеоогляд** → повідомлення з ПІБ клієнта та бажаним часом

---

## 🖥 Деплой на сервер (Ubuntu/Debian)

### Варіант А — systemd (рекомендовано)

```bash
sudo nano /etc/systemd/system/otsinka24bot.service
```

Вміст:
```ini
[Unit]
Description=ОЦІНКА24 Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/otsinka24_bot
EnvironmentFile=/home/ubuntu/otsinka24_bot/.env
ExecStart=/home/ubuntu/otsinka24_bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable otsinka24bot
sudo systemctl start otsinka24bot
sudo systemctl status otsinka24bot
```

### Варіант Б — Docker

```bash
# Dockerfile (простий приклад):
# FROM python:3.11-slim
# WORKDIR /app
# COPY requirements.txt .
# RUN pip install -r requirements.txt
# COPY . .
# CMD ["python", "bot.py"]

docker build -t otsinka24bot .
docker run -d --env-file .env --name otsinka24bot otsinka24bot
```

---

## 💰 Хостинг (рекомендації)

| Варіант            | Ціна/міс  | Підходить для               |
|--------------------|-----------|-----------------------------|
| VPS Hetzner CX11   | ~4 EUR    | Постійна робота ✅           |
| VPS DigitalOcean   | ~6 USD    | Постійна робота ✅           |
| Railway.app        | Безкоштов. | Тест / малий трафік ✅      |
| Render.com         | Безкоштов. | Тест (засинає) ⚠️           |

---

## 🔒 Безпека

- `.env` файл — **ніколи** не завантажувати в Git
- Додайте `.env` до `.gitignore`
- Регулярно оновлюйте `python-telegram-bot`

---

## 📞 Підтримка

Питання: info@otsinka24.ua
