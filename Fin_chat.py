import datetime
import json
import os
import re
import requests
from abc import ABC, abstractmethod
import telebot


class Config:
    DATA_FILE = "finance_data.json"
    TOKEN = "8325206325:AAFaNadFOV80OhboMUs4n1ZwfUJdbGRG72c"
    DEFAULT_LIMITS = {
        "еда": 50000,
        "транспорт": 20000,
        "развлечения": 30000,
        "одежда": 40000,
    }


class Transaction(ABC):
    def __init__(self, amount: float, category: str, description: str):
        self._amount = amount
        self.category = category
        self.description = description
        self.date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    @abstractmethod
    def get_details(self) -> str:
        pass

    def to_dict(self) -> dict:
        return {
            "amount": self._amount,
            "category": self.category,
            "description": self.description,
            "date": self.date,
        }


class Expense(Transaction):
    def get_details(self) -> str:
        return f"{self.date} | {self.description}: -{self._amount:.2f} тг ({self.category})"


class Income(Transaction):
    def get_details(self) -> str:
        return f"{self.date} | {self.description}: +{self._amount:.2f} тг ({self.category})"


class Goal:
    def __init__(self, name: str, target: float):
        self.name = name
        self.target = target
        self.saved = 0.0

    def add(self, amount: float):
        self.saved += amount

    def progress(self) -> float:
        return min(100.0, (self.saved / self.target) * 100) if self.target > 0 else 0

    def remaining(self) -> float:
        return max(0.0, self.target - self.saved)

    def to_dict(self) -> dict:
        return {"name": self.name, "target": self.target, "saved": self.saved}

    @staticmethod
    def from_dict(d: dict) -> "Goal":
        g = Goal(d["name"], d["target"])
        g.saved = d.get("saved", 0.0)
        return g


class UserData:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.name = ""
        self.balance = 0.0
        self.expenses: list[dict] = []
        self.goals: list[Goal] = []
        self.smart_map: dict[str, str] = {}
        self.limits: dict[str, float] = dict(Config.DEFAULT_LIMITS)

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "name": self.name,
            "balance": self.balance,
            "expenses": self.expenses,
            "goals": [g.to_dict() for g in self.goals],
            "smart_map": self.smart_map,
            "limits": self.limits,
        }

    @staticmethod
    def from_dict(d: dict) -> "UserData":
        u = UserData(d["chat_id"])
        u.name = d.get("name", "")
        u.balance = d.get("balance", 0.0)
        u.expenses = d.get("expenses", [])
        u.goals = [Goal.from_dict(g) for g in d.get("goals", [])]
        u.smart_map = d.get("smart_map", {})
        u.limits = d.get("limits", dict(Config.DEFAULT_LIMITS))
        return u


class Storage:
    FILE = Config.DATA_FILE

    @staticmethod
    def load_all() -> dict[int, UserData]:
        if not os.path.exists(Storage.FILE):
            return {}
        with open(Storage.FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): UserData.from_dict(v) for k, v in raw.items()}

    @staticmethod
    def save_all(users: dict[int, UserData]):
        raw = {str(k): v.to_dict() for k, v in users.items()}
        with open(Storage.FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)


def get_usd_rate() -> float:
    try:
        r = requests.get(
            "https://api.exchangerate-api.com/v4/latest/USD",
            timeout=5
        )
        data = r.json()
        return data["rates"].get("KZT", 450.0)
    except Exception:
        return 450.0


def parse_expense_message(text: str) -> tuple[float, str] | None:
    patterns = [
        r"расход\s+(\d+[\.,]?\d*)\s+(.+)",
        r"(\d+[\.,]?\d*)\s*тг\s+(.+)",
        r"потратил\s+(\d+[\.,]?\d*)\s+на\s+(.+)",
        r"купил\s+(.+)\s+за\s+(\d+[\.,]?\d*)",
    ]
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, text.lower())
        if match:
            if i == 3:
                desc, amount_str = match.group(1), match.group(2)
            else:
                amount_str, desc = match.group(1), match.group(2)
            amount = float(amount_str.replace(",", "."))
            return amount, desc.strip()
    return None


def sort_expenses(expenses: list[dict], by: str = "date", reverse: bool = True) -> list[dict]:
    if by == "amount":
        return sorted(expenses, key=lambda x: x["amount"], reverse=reverse)
    return sorted(expenses, key=lambda x: x["date"], reverse=reverse)


def check_limit_warning(user: UserData, category: str, amount: float) -> str | None:
    if category not in user.limits:
        return None
    now = datetime.datetime.now()
    spent = sum(
        e["amount"] for e in user.expenses
        if e["category"] == category
        and datetime.datetime.strptime(e["date"], "%Y-%m-%d %H:%M").month == now.month
        and datetime.datetime.strptime(e["date"], "%Y-%m-%d %H:%M").year == now.year
    )
    spent += amount
    limit = user.limits[category]
    if spent >= limit:
        return f"Лимит по '{category}' превышен! {spent:.0f} / {limit:.0f} тг"
    elif spent >= limit * 0.8:
        return f"Внимание! Израсходовано {spent:.0f} из {limit:.0f} тг по '{category}' (80%+)"
    return None


users_db: dict[int, UserData] = Storage.load_all()


def get_user(chat_id: int) -> UserData:
    if chat_id not in users_db:
        users_db[chat_id] = UserData(chat_id)
    return users_db[chat_id]


def save():
    Storage.save_all(users_db)


bot = telebot.TeleBot(Config.TOKEN)

user_states: dict[int, dict] = {}


def set_state(chat_id: int, state: str, data: dict = None):
    user_states[chat_id] = {"state": state, "data": data or {}}


def get_state(chat_id: int) -> dict:
    return user_states.get(chat_id, {})


def clear_state(chat_id: int):
    user_states.pop(chat_id, None)


def main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💰 Баланс", "➕ Доход", "➖ Расход")
    markup.row("📊 Статистика", "🎯 Цели", "⚙️ Лимиты")
    markup.row("📋 История", "💱 Курс валют", "❓ Помощь")
    return markup


@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user.name:
        user.name = message.from_user.first_name or "друг"
        save()
    bot.send_message(
        chat_id,
        f"Привет, {user.name}! Я твой финансовый помощник 💸\n\n"
        f"Используй кнопки ниже или пиши:\n"
        f"расход 1500 донер\n"
        f"потратил 2000 на такси\n"
        f"купил кофе за 800",
        reply_markup=main_keyboard()
    )


@bot.message_handler(commands=["balance"])
@bot.message_handler(func=lambda m: m.text == "💰 Баланс")
def cmd_balance(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    rate = get_usd_rate()
    usd = user.balance / rate
    bot.send_message(
        chat_id,
        f"💰 Твой баланс:\n"
        f"{user.balance:.2f} тг\n"
        f"≈ {usd:.2f} USD (курс: {rate:.1f} тг/$)"
    )


@bot.message_handler(commands=["history"])
@bot.message_handler(func=lambda m: m.text == "📋 История")
def cmd_history(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user.expenses:
        bot.send_message(chat_id, "Расходов пока нет.")
        return
    sorted_exp = sort_expenses(user.expenses, by="date", reverse=True)[:10]
    text = "📋 Последние расходы:\n\n"
    for e in sorted_exp:
        text += f"[{e['id']}] {e['date']}\n{e['description']} — {e['amount']:.0f} тг ({e['category']})\n\n"
    bot.send_message(chat_id, text)


@bot.message_handler(commands=["stats"])
@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def cmd_stats(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user.expenses:
        bot.send_message(chat_id, "Данных нет.")
        return
    now = datetime.datetime.now()
    filtered = [
        e for e in user.expenses
        if datetime.datetime.strptime(e["date"], "%Y-%m-%d %H:%M").month == now.month
        and datetime.datetime.strptime(e["date"], "%Y-%m-%d %H:%M").year == now.year
    ]
    if not filtered:
        bot.send_message(chat_id, "В этом месяце расходов нет.")
        return
    category_totals = {}
    total = 0
    for e in filtered:
        category_totals[e["category"]] = category_totals.get(e["category"], 0) + e["amount"]
        total += e["amount"]
    sorted_cats = sorted(category_totals.items(), key=lambda x: -x[1])
    text = f"📊 Статистика за {now.strftime('%B %Y')}:\nВсего потрачено: {total:.0f} тг\n\n"
    for cat, amount in sorted_cats:
        percent = (amount / total) * 100
        limit = user.limits.get(cat)
        limit_info = f" / лимит {limit:.0f}" if limit else ""
        bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
        text += f"{cat.capitalize()}: {amount:.0f} тг ({percent:.1f}%){limit_info}\n{bar}\n\n"
    bot.send_message(chat_id, text)


@bot.message_handler(commands=["goals"])
@bot.message_handler(func=lambda m: m.text == "🎯 Цели")
def cmd_goals(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user.goals:
        bot.send_message(chat_id, "Целей нет. Добавь через /add_goal")
        return
    text = "🎯 Твои цели:\n\n"
    for i, g in enumerate(user.goals, 1):
        bar = "█" * int(g.progress() / 10) + "░" * (10 - int(g.progress() / 10))
        status = "Можно покупать! 🎉" if g.saved >= g.target else f"осталось {g.remaining():.0f} тг"
        text += f"{i}. {g.name}\n{bar} {g.progress():.1f}%\n{g.saved:.0f} / {g.target:.0f} тг — {status}\n\n"
    bot.send_message(chat_id, text)


@bot.message_handler(commands=["add_goal"])
def cmd_add_goal(message):
    chat_id = message.chat.id
    set_state(chat_id, "add_goal_name")
    bot.send_message(chat_id, "На что копим? Напиши название цели:")


@bot.message_handler(commands=["save_to_goal"])
def cmd_save_to_goal(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user.goals:
        bot.send_message(chat_id, "Целей нет. Сначала /add_goal")
        return
    text = "Выбери номер цели:\n\n"
    for i, g in enumerate(user.goals, 1):
        text += f"{i}. {g.name} — {g.saved:.0f}/{g.target:.0f} тг\n"
    set_state(chat_id, "save_to_goal_pick")
    bot.send_message(chat_id, text)


@bot.message_handler(commands=["limits"])
@bot.message_handler(func=lambda m: m.text == "⚙️ Лимиты")
def cmd_limits(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    now = datetime.datetime.now()
    text = "⚙️ Лимиты на этот месяц:\n\n"
    for cat, limit in user.limits.items():
        spent = sum(
            e["amount"] for e in user.expenses
            if e["category"] == cat
            and datetime.datetime.strptime(e["date"], "%Y-%m-%d %H:%M").month == now.month
        )
        percent = (spent / limit * 100) if limit > 0 else 0
        bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
        text += f"{cat.capitalize()}: {spent:.0f} / {limit:.0f} тг\n{bar} {percent:.1f}%\n\n"
    text += "Чтобы изменить лимит: /set_limit"
    bot.send_message(chat_id, text)


@bot.message_handler(commands=["set_limit"])
def cmd_set_limit(message):
    chat_id = message.chat.id
    set_state(chat_id, "set_limit_category")
    bot.send_message(chat_id, "Напиши категорию и новый лимит через пробел.\nНапример: еда 60000")


@bot.message_handler(func=lambda m: m.text == "💱 Курс валют")
def cmd_currency(message):
    chat_id = message.chat.id
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        data = r.json()
        kzt = data["rates"].get("KZT", "—")
        rub = data["rates"].get("RUB", "—")
        eur_kzt = data["rates"].get("KZT", 450) / data["rates"].get("EUR", 1)
        bot.send_message(
            chat_id,
            f"💱 Курс валют:\n\n"
            f"1 USD = {kzt:.2f} тг\n"
            f"1 EUR ≈ {eur_kzt:.2f} тг\n"
            f"1 USD = {rub:.2f} ₽"
        )
    except Exception:
        bot.send_message(chat_id, "Не удалось получить курс. Попробуй позже.")


@bot.message_handler(func=lambda m: m.text in ["➕ Доход", "➖ Расход", "❓ Помощь"])
def cmd_buttons(message):
    chat_id = message.chat.id
    if message.text == "➕ Доход":
        set_state(chat_id, "add_income_amount")
        bot.send_message(chat_id, "Сколько получил? Напиши сумму:")
    elif message.text == "➖ Расход":
        set_state(chat_id, "add_expense_desc")
        bot.send_message(chat_id, "Что купил? Напиши описание:")
    elif message.text == "❓ Помощь":
        bot.send_message(
            chat_id,
            "📌 Команды:\n\n"
            "/balance — баланс\n"
            "/stats — статистика\n"
            "/history — история расходов\n"
            "/goals — цели\n"
            "/add_goal — новая цель\n"
            "/save_to_goal — пополнить цель\n"
            "/limits — лимиты по категориям\n"
            "/set_limit — изменить лимит\n\n"
            "Быстрый расход:\n"
            "расход 1500 донер\n"
            "потратил 2000 на такси\n"
            "купил кофе за 800"
        )


@bot.message_handler(func=lambda message: True)
def handle_all(message):
    chat_id = message.chat.id
    text = message.text.strip()
    state = get_state(chat_id)

    if state.get("state") == "add_income_amount":
        try:
            amount = float(text.replace(",", "."))
            set_state(chat_id, "add_income_source", {"amount": amount})
            bot.send_message(chat_id, "Источник дохода (например: зарплата):")
        except ValueError:
            bot.send_message(chat_id, "Введи число.")
        return

    if state.get("state") == "add_income_source":
        amount = state["data"]["amount"]
        user = get_user(chat_id)
        income = Income(amount, "доход", text)
        user.balance += amount
        save()
        clear_state(chat_id)
        bot.send_message(chat_id, f"✅ {income.get_details()}\nБаланс: {user.balance:.2f} тг")
        return

    if state.get("state") == "add_expense_desc":
        set_state(chat_id, "add_expense_amount", {"desc": text.lower()})
        bot.send_message(chat_id, f"Сколько стоил '{text}'?")
        return

    if state.get("state") == "add_expense_amount":
        try:
            amount = float(text.replace(",", "."))
            desc = state["data"]["desc"]
            user = get_user(chat_id)
            category = user.smart_map.get(desc, None)
            if category:
                _add_expense(chat_id, amount, desc, category)
                clear_state(chat_id)
            else:
                set_state(chat_id, "add_expense_category", {"desc": desc, "amount": amount})
                bot.send_message(chat_id, f"Не знаю '{desc}'. Какая категория?\n(еда, транспорт, развлечения, одежда, другое)")
        except ValueError:
            bot.send_message(chat_id, "Введи число.")
        return

    if state.get("state") == "add_expense_category":
        category = text.lower()
        data = state["data"]
        user = get_user(chat_id)
        user.smart_map[data["desc"]] = category
        save()
        _add_expense(chat_id, data["amount"], data["desc"], category)
        clear_state(chat_id)
        return

    if state.get("state") == "add_goal_name":
        set_state(chat_id, "add_goal_amount", {"name": text})
        bot.send_message(chat_id, f"Сколько стоит '{text}'?")
        return

    if state.get("state") == "add_goal_amount":
        try:
            target = float(text.replace(",", "."))
            user = get_user(chat_id)
            goal = Goal(state["data"]["name"], target)
            user.goals.append(goal)
            save()
            clear_state(chat_id)
            bot.send_message(chat_id, f"🎯 Цель '{goal.name}' добавлена! Нужно накопить: {target:.0f} тг")
        except ValueError:
            bot.send_message(chat_id, "Введи число.")
        return

    if state.get("state") == "save_to_goal_pick":
        try:
            idx = int(text) - 1
            user = get_user(chat_id)
            if idx < 0 or idx >= len(user.goals):
                bot.send_message(chat_id, "Неверный номер.")
                return
            set_state(chat_id, "save_to_goal_amount", {"idx": idx})
            bot.send_message(chat_id, f"Сколько откладываем на '{user.goals[idx].name}'?")
        except ValueError:
            bot.send_message(chat_id, "Введи номер.")
        return

    if state.get("state") == "save_to_goal_amount":
        try:
            amount = float(text.replace(",", "."))
            user = get_user(chat_id)
            idx = state["data"]["idx"]
            if amount > user.balance:
                bot.send_message(chat_id, f"Недостаточно средств. Баланс: {user.balance:.0f} тг")
                return
            user.goals[idx].add(amount)
            user.balance -= amount
            save()
            clear_state(chat_id)
            g = user.goals[idx]
            bot.send_message(
                chat_id,
                f"✅ Отложено {amount:.0f} тг на '{g.name}'\n"
                f"Прогресс: {g.progress():.1f}% ({g.saved:.0f}/{g.target:.0f} тг)\n"
                f"Баланс: {user.balance:.0f} тг"
            )
        except ValueError:
            bot.send_message(chat_id, "Введи число.")
        return

    if state.get("state") == "set_limit_category":
        parts = text.split()
        if len(parts) < 2:
            bot.send_message(chat_id, "Формат: категория сумма\nНапример: еда 60000")
            return
        try:
            category = parts[0].lower()
            limit = float(parts[1].replace(",", "."))
            user = get_user(chat_id)
            user.limits[category] = limit
            save()
            clear_state(chat_id)
            bot.send_message(chat_id, f"✅ Лимит для '{category}' установлен: {limit:.0f} тг/месяц")
        except ValueError:
            bot.send_message(chat_id, "Введи сумму числом.")
        return

    parsed = parse_expense_message(text)
    if parsed:
        amount, desc = parsed
        user = get_user(chat_id)
        category = user.smart_map.get(desc, None)
        if category:
            _add_expense(chat_id, amount, desc, category)
        else:
            set_state(chat_id, "add_expense_category", {"desc": desc, "amount": amount})
            bot.send_message(chat_id, f"Категория для '{desc}'?\n(еда, транспорт, развлечения, одежда, другое)")
        return

    bot.send_message(chat_id, "Не понял. Нажми ❓ Помощь или используй кнопки.", reply_markup=main_keyboard())


def _add_expense(chat_id: int, amount: float, description: str, category: str):
    user = get_user(chat_id)
    expense = Expense(amount, category, description)
    user.expenses.append({
        "id": len(user.expenses) + 1,
        **expense.to_dict(),
    })
    user.balance -= amount
    save()
    warning = check_limit_warning(user, category, 0)
    reply = f"✅ {expense.get_details()}\nБаланс: {user.balance:.2f} тг"
    if warning:
        reply += f"\n\n⚠️ {warning}"
    bot.send_message(chat_id, reply)


if __name__ == "__main__":
    print("Бот запущен...")
    bot.polling(non_stop=True)
