import pandas as pd
import sqlite3
import re
import hashlib
from flask import Flask, request, jsonify, render_template, redirect, url_for, session

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a secure key in production

# --- 1. Unit Conversion Dictionary ---
UNIT_CONVERSIONS = {
    'g': 1, 'gram': 1, 'grams': 1,
    'kg': 1000, 'kilogram': 1000,
    'mg': 0.001, 'milligram': 0.001,
    'oz': 28.35, 'ounce': 28.35,
    'lb': 453.6, 'pound': 453.6,
    'ml': 1, 'l': 1000, 'liter': 1000,
    'cup': 240, 'tbsp': 15, 'tablespoon': 15,
    'tsp': 5, 'teaspoon': 5,
    'piece': 50, 'pcs': 50, 'pinch': 1,
    'clove': 5, 'slice': 30, 'can': 400, 'stick': 113
}

def measure_to_grams(measure, scale_factor=1.0):
    if not isinstance(measure, str) or not measure.strip():
        return None
    m = re.match(r'(?P<qty>\d+(\.\d+)?)(\s+)?(?P<unit>\w+)?', measure.lower())
    if m:
        qty = float(m.group('qty')) * scale_factor
        unit = m.group('unit') or 'g'
        unit = unit.strip().lower()
        if unit in UNIT_CONVERSIONS:
            return qty * UNIT_CONVERSIONS[unit]
        if unit.endswith('s') and unit[:-1] in UNIT_CONVERSIONS:
            return qty * UNIT_CONVERSIONS[unit[:-1]]
    frac_match = re.match(r'(?P<whole>\d+)?\s?(?P<frac>\d+/\d+)\s?(?P<unit>\w+)?', measure.lower())
    if frac_match:
        whole = float(frac_match.group('whole')) if frac_match.group('whole') else 0
        num, denom = map(float, frac_match.group('frac').split('/'))
        qty = (whole + num/denom) * scale_factor
        unit = frac_match.group('unit') or 'g'
        unit = unit.strip().lower()
        if unit in UNIT_CONVERSIONS:
            return qty * UNIT_CONVERSIONS[unit]
        if unit.endswith('s') and unit[:-1] in UNIT_CONVERSIONS:
            return qty * UNIT_CONVERSIONS[unit[:-1]]
    return None

# --- 2. Setup Database ---
def setup_database():
    conn = sqlite3.connect('recipes_final.db')
    c = conn.cursor()

    # Auth User Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS auth_user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    c.execute("INSERT OR IGNORE INTO auth_user (username, email, password, is_admin) VALUES (?, ?, ?, ?)",
              ('admin', 'admin@example.com', hashlib.sha256('adminpassword'.encode()).hexdigest(), 1))

    # Ingredient Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS ingredient (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        unit TEXT,
        calories_per_unit REAL,
        description TEXT,
        image TEXT
    );
    """)

    # Recipe Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS recipe (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT,
        description TEXT,
        image TEXT,
        author INTEGER,
        instruction_steps TEXT,
        servings INTEGER,
        total_calories REAL,
        created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(author) REFERENCES auth_user(id)
    );
    """)

    # Recipe-Ingredient Linking Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS recipe_ingredient (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipe_id INTEGER,
        ingredient_id INTEGER,
        quantity_per_serving TEXT,
        unit TEXT,
        grams REAL,
        calories REAL,
        FOREIGN KEY(recipe_id) REFERENCES recipe(id),
        FOREIGN KEY(ingredient_id) REFERENCES ingredient(id)
    );
    """)

    # Recipe Images Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS recipe_image (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipe_id INTEGER,
        image TEXT,
        FOREIGN KEY(recipe_id) REFERENCES recipe(id)
    );
    """)

    conn.commit()
    return conn

# --- 3. Initialize Data ---
def initialize_data(conn):
    c = conn.cursor()

    # Read CSV files
    ingredients_df = pd.read_csv('ingredients.csv')
    recipe_ingredient_df = pd.read_csv('recipe_ingredient.csv')
    recipes_df = pd.read_csv('recipes.csv')

    # Compute Grams and Calories
    ingredients_df['calories_per_g'] = pd.to_numeric(ingredients_df['calories_per_unit'], errors='coerce') / 100
    calories_per_g = dict(zip(ingredients_df['id'], ingredients_df['calories_per_g']))

    grams_list = []
    calories_list = []
    for idx, row in recipe_ingredient_df.iterrows():
        ingredient_id = row['ingredient_id']
        measure = row['quantity_per_serving']
        grams = measure_to_grams(str(measure))
        grams_list.append(grams)
        if grams is not None and ingredient_id in calories_per_g and pd.notnull(calories_per_g[ingredient_id]):
            calories_list.append(grams * calories_per_g[ingredient_id])
        else:
            calories_list.append(None)
    recipe_ingredient_df['grams'] = grams_list
    recipe_ingredient_df['calories'] = calories_list

    # Compute total calories per recipe
    recipe_total_calories = recipe_ingredient_df.groupby('recipe_id')['calories'].sum().reset_index().rename(columns={'calories': 'total_calories'})
    recipes_df['author'] = recipes_df['author'].fillna(1)  # Default to admin for imported recipes
    recipe_total_calories = recipe_total_calories.rename(columns={'recipe_id': 'id'})
    recipes_df = recipes_df.merge(recipe_total_calories, on='id', how='left').fillna({'total_calories': 0})

    # Insert Data
    ingredients_df[['id', 'name', 'unit', 'calories_per_unit', 'description', 'image']].to_sql('ingredient', conn, if_exists='replace', index=False)
    recipes_df[['id', 'name', 'type', 'description', 'image', 'author', 'instruction_steps', 'servings', 'total_calories']].to_sql('recipe', conn, if_exists='replace', index=False)
    recipe_ingredient_df[['recipe_id', 'ingredient_id', 'quantity_per_serving', 'unit', 'grams', 'calories']].to_sql('recipe_ingredient', conn, if_exists='replace', index=False)

    conn.commit()

# --- 4. Authentication Decorator ---
def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__  # Preserve the original function name
    return decorated_function

# --- 5. Flask Routes ---
@app.route('/')
def home():
    conn = sqlite3.connect('recipes_final.db')
    recipes = pd.read_sql_query("SELECT * FROM recipe", conn)
    conn.close()
    return render_template('recipe_list.html', recipes=recipes.to_dict(orient='records'))

@app.route('/recipe/<int:recipe_id>')
def recipe_detail(recipe_id):
    conn = sqlite3.connect('recipes_final.db')
    recipe = pd.read_sql_query("SELECT * FROM recipe WHERE id = ?", conn, params=(recipe_id,))
    ingredients = pd.read_sql_query("""
        SELECT i.name, i.unit, i.calories_per_unit, ri.quantity_per_serving, ri.calories
        FROM recipe_ingredient ri
        JOIN ingredient i ON ri.ingredient_id = i.id
        WHERE ri.recipe_id = ?
    """, conn, params=(recipe_id,))
    conn.close()
    return render_template('recipe_detail.html', recipe=recipe.iloc[0].to_dict(), ingredients=ingredients.to_dict(orient='records'))

@app.route('/add_recipe', methods=['GET', 'POST'], endpoint='add_recipe')
@login_required
def add_recipe_endpoint():
    if request.method == 'POST':
        conn = sqlite3.connect('recipes_final.db')
        c = conn.cursor()
        c.execute("INSERT INTO recipe (name, type, description, image, author, instruction_steps, servings, total_calories) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (request.form['name'], request.form['type'], request.form['description'], request.form['image'], session['user_id'], request.form['instruction_steps'], request.form['servings'], 0))
        recipe_id = c.lastrowid
        for img in request.form.getlist('images[]'):
            c.execute("INSERT INTO recipe_image (recipe_id, image) VALUES (?, ?)", (recipe_id, img))
        conn.commit()
        conn.close()
        return redirect(url_for('home'))
    return render_template('add_recipe.html')

@app.route('/edit_recipe/<int:recipe_id>', methods=['GET', 'POST'], endpoint='edit_recipe')
@login_required
def edit_recipe_endpoint(recipe_id):
    conn = sqlite3.connect('recipes_final.db')
    recipe = pd.read_sql_query("SELECT * FROM recipe WHERE id = ?", conn, params=(recipe_id,)).iloc[0]
    if recipe['author'] != session['user_id']:
        return "Unauthorized", 403
    if request.method == 'POST':
        c = conn.cursor()
        c.execute("""
            UPDATE recipe SET name = ?, type = ?, description = ?, instruction_steps = ?, servings = ?
            WHERE id = ? AND author = ?
        """, (request.form['name'], request.form['type'], request.form['description'], request.form['instruction_steps'], request.form['servings'], recipe_id, session['user_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('recipe_detail', recipe_id=recipe_id))
    images = pd.read_sql_query("SELECT image FROM recipe_image WHERE recipe_id = ?", conn, params=(recipe_id,))
    conn.close()
    return render_template('edit_recipe.html', recipe=recipe, images=images['image'].tolist())

@app.route('/ingredients')
def ingredients_list():
    conn = sqlite3.connect('recipes_final.db')
    ingredients = pd.read_sql_query("SELECT id, name, unit, calories_per_unit, description, image FROM ingredient WHERE name LIKE ?", conn, params=(f'%{request.args.get("q", "")}%',))
    conn.close()
    return render_template('ingredients.html', ingredients=ingredients.to_dict(orient='records'))

@app.route('/add_ingredient', methods=['GET', 'POST'], endpoint='add_ingredient')
@login_required
def add_ingredient_endpoint():
    if 'user_id' not in session or not session.get('is_admin', False):
        return redirect(url_for('login'))
    if request.method == 'POST':
        conn = sqlite3.connect('recipes_final.db')
        c = conn.cursor()
        c.execute("INSERT INTO ingredient (name, unit, calories_per_unit, description, image) VALUES (?, ?, ?, ?, ?)",
                  (request.form['name'], request.form['unit'], request.form['calories_per_unit'], request.form['description'], request.form['image'] if 'image' in request.form else ''))
        conn.commit()
        conn.close()
        return redirect(url_for('ingredients'))
    return render_template('add_ingredient.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = sqlite3.connect('recipes_final.db')
        c = conn.cursor()
        c.execute("SELECT id, username, is_admin FROM auth_user WHERE username = ? AND password = ?", 
                  (request.form['username'], hashlib.sha256(request.form['password'].encode()).hexdigest()))
        user = c.fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['is_admin'] = bool(user[2])
            return redirect(url_for('home'))
        return "Invalid credentials", 401
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        conn = sqlite3.connect('recipes_final.db')
        c = conn.cursor()
        try:
            c.execute("INSERT INTO auth_user (username, email, password) VALUES (?, ?, ?)",
                      (request.form['username'], request.form['email'], hashlib.sha256(request.form['password'].encode()).hexdigest()))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return "Username or email already exists", 400
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/profile', endpoint='profile')
@login_required
def profile_endpoint():
    conn = sqlite3.connect('recipes_final.db')
    user = pd.read_sql_query("SELECT username, email FROM auth_user WHERE id = ?", conn, params=(session['user_id'],)).iloc[0]
    conn.close()
    return render_template('profile.html', user=user)

@app.route('/edit_profile', methods=['GET', 'POST'], endpoint='edit_profile')
@login_required
def edit_profile_endpoint():
    conn = sqlite3.connect('recipes_final.db')
    user = pd.read_sql_query("SELECT username, email FROM auth_user WHERE id = ?", conn, params=(session['user_id'],)).iloc[0]
    if request.method == 'POST':
        c = conn.cursor()
        c.execute("UPDATE auth_user SET username = ?, email = ? WHERE id = ?",
                  (request.form['username'], request.form['email'], session['user_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('profile'))
    conn.close()
    return render_template('edit_profile.html', user=user)

@app.route('/search_by_ingredient', methods=['GET', 'POST'], endpoint='search_by_ingredient')
@login_required
def search_by_ingredient_endpoint():
    if request.method == 'POST':
        ingredient_name = request.form['ingredient_name']
        conn = sqlite3.connect('recipes_final.db')
        recipes = pd.read_sql_query("""
            SELECT r.* FROM recipe r
            JOIN recipe_ingredient ri ON r.id = ri.recipe_id
            JOIN ingredient i ON ri.ingredient_id = i.id
            WHERE i.name LIKE ?
        """, conn, params=(f'%{ingredient_name}%',))
        conn.close()
        return render_template('search_by_ingredient.html', recipes=recipes.to_dict(orient='records'))
    return render_template('search_by_ingredient.html')

@app.route('/admin', endpoint='admin_panel')
@login_required
def admin_panel_endpoint():
    if 'user_id' not in session or not session.get('is_admin', False):
        return redirect(url_for('login'))
    conn = sqlite3.connect('recipes_final.db')
    users = pd.read_sql_query("SELECT id, username, email, is_admin FROM auth_user", conn)
    conn.close()
    return render_template('admin_panel.html', users=users.to_dict(orient='records'))

@app.route('/api/recipes')
def api_recipes():
    search_query = request.args.get('q', '')
    conn = sqlite3.connect('recipes_final.db')
    recipes = pd.read_sql_query("SELECT id, name, type, description, total_calories FROM recipe WHERE name LIKE ? OR type LIKE ?", params=(f'%{search_query}%', f'%{search_query}%'))
    conn.close()
    return jsonify(recipes.to_dict(orient='records'))

@app.route('/api/ingredients')
def api_ingredients():
    search_query = request.args.get('q', '')
    conn = sqlite3.connect('recipes_final.db')
    ingredients = pd.read_sql_query("SELECT id, name, unit, calories_per_unit, description, image FROM ingredient WHERE name LIKE ?", params=(f'%{search_query}%',))
    conn.close()
    return jsonify(ingredients.to_dict(orient='records'))

@app.route('/scale_recipe/<int:recipe_id>/<float:factor>', methods=['POST'], endpoint='scale_recipe')
@login_required
def scale_recipe_endpoint(recipe_id, factor):
    conn = sqlite3.connect('recipes_final.db')
    c = conn.cursor()
    c.execute("SELECT author FROM recipe WHERE id = ?", (recipe_id,))
    author = c.fetchone()[0]
    if author != session['user_id']:
        return "Unauthorized", 403
    c.execute("UPDATE recipe SET servings = servings * ? WHERE id = ?", (factor, recipe_id))
    c.execute("UPDATE recipe_ingredient SET quantity_per_serving = ?, grams = ? WHERE recipe_id = ?",
              (lambda q: f"{float(q.split()[0]) * factor}{q.split()[1]}" if len(q.split()) > 1 else f"{float(q) * factor}g", 
               lambda g: g * factor if g else None, recipe_id))
    conn.commit()
    conn.close()
    return redirect(url_for('recipe_detail', recipe_id=recipe_id))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('is_admin', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    conn = setup_database()
    initialize_data(conn)
    conn.close()
    app.run(debug=True)