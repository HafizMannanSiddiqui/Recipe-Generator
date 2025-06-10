import pandas as pd
import sqlite3
import re
import hashlib
from py4web import action, request, response, redirect, URL
from py4web.utils.auth import auth
from pydal import DAL
from yatl.helpers import A, DIV, H1, INPUT, FORM, BUTTON, UL, LI, P

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
    try:
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
    except (ValueError, AttributeError):
        return None

# --- 2. Setup Database ---
def setup_database():
    db = DAL('sqlite://recipes_final.db', folder=None)
    db.define_table('auth_user',
        db.Field('username', 'string', unique=True, notnull=True),
        db.Field('email', 'string', unique=True, notnull=True),
        db.Field('password', 'string', notnull=True),
        db.Field('is_admin', 'boolean', default=False),
        db.Field('created_on', 'datetime', default='CURRENT_TIMESTAMP')
    )
    db.auth_user.insert_or_ignore(username='admin', email='admin@example.com', password=hashlib.sha256('adminpassword'.encode()).hexdigest(), is_admin=True)

    db.define_table('ingredient',
        db.Field('name', 'string', unique=True, notnull=True),
        db.Field('unit', 'string'),
        db.Field('calories_per_unit', 'float'),
        db.Field('description', 'string'),
        db.Field('image', 'string')
    )

    db.define_table('recipe',
        db.Field('name', 'string', notnull=True),
        db.Field('type', 'string'),
        db.Field('description', 'string'),
        db.Field('image', 'string'),
        db.Field('author', 'reference auth_user'),
        db.Field('instruction_steps', 'string'),
        db.Field('servings', 'integer'),
        db.Field('total_calories', 'float'),
        db.Field('created_on', 'datetime', default='CURRENT_TIMESTAMP')
    )

    db.define_table('recipe_ingredient',
        db.Field('recipe_id', 'reference recipe'),
        db.Field('ingredient_id', 'reference ingredient'),
        db.Field('quantity_per_serving', 'string'),
        db.Field('unit', 'string'),
        db.Field('grams', 'float'),
        db.Field('calories', 'float')
    )

    db.define_table('recipe_image',
        db.Field('recipe_id', 'reference recipe'),
        db.Field('image', 'string')
    )

    db.commit()
    return db

# --- 3. Initialize Data ---
def initialize_data(db):
    try:
        ingredients_df = pd.read_csv('ingredients.csv')
        recipe_ingredient_df = pd.read_csv('recipe_ingredient.csv')
        recipes_df = pd.read_csv('recipes.csv')
        required_cols = {'ingredients': ['id', 'name', 'unit', 'calories_per_unit'],
                         'recipe_ingredient': ['recipe_id', 'ingredient_id', 'quantity_per_serving'],
                         'recipes': ['id', 'name', 'type', 'description']}
        for df, cols in required_cols.items():
            if not all(col in globals()[f'{df}_df'].columns for col in cols):
                raise ValueError(f"Missing columns in {df}.csv")
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

        recipe_total_calories = recipe_ingredient_df.groupby('recipe_id')['calories'].sum().reset_index().rename(columns={'calories': 'total_calories'})
        recipes_df['author'] = recipes_df['author'].fillna(1)
        recipe_total_calories = recipe_total_calories.rename(columns={'recipe_id': 'id'})
        recipes_df = recipes_df.merge(recipe_total_calories, on='id', how='left').fillna({'total_calories': 0})

        db.ingredient.truncate()
        db.recipe.truncate()
        db.recipe_ingredient.truncate()
        db.ingredient.bulk_insert(ingredients_df[['id', 'name', 'unit', 'calories_per_unit', 'description', 'image']].to_dict(orient='records'))
        db.recipe.bulk_insert(recipes_df[['id', 'name', 'type', 'description', 'image', 'author', 'instruction_steps', 'servings', 'total_calories']].to_dict(orient='records'))
        db.recipe_ingredient.bulk_insert(recipe_ingredient_df[['recipe_id', 'ingredient_id', 'quantity_per_serving', 'unit', 'grams', 'calories']].to_dict(orient='records'))
        db.commit()
    except Exception as e:
        print(f"Error initializing data: {e}")
        db.rollback()

# --- 4. py4web Controllers ---
@action('index')
@action.uses('recipe_list.html', auth.user)
def home():
    db = DAL('sqlite://recipes_final.db', folder=None)
    recipes = db(db.recipe).select().as_list()
    print(f"Debug: Number of recipes = {len(recipes)}, Recipes = {recipes}")
    db.close()
    return dict(recipes=recipes)

@action('recipe/<recipe_id:int>')
@action.uses('recipe_detail.html', auth.user)
def recipe_detail(recipe_id):
    db = DAL('sqlite://recipes_final.db', folder=None)
    recipe = db(db.recipe.id == recipe_id).select().first().as_dict()
    ingredients = db(db.recipe_ingredient.recipe_id == recipe_id).join(db.ingredient).select(
        db.ingredient.name,
        db.ingredient.unit,
        db.ingredient.calories_per_unit,
        db.recipe_ingredient.quantity_per_serving,
        db.recipe_ingredient.calories
    ).as_list()
    db.close()
    return dict(recipe=recipe, ingredients=ingredients)

@action('add_recipe', method=['GET', 'POST'])
@action.uses('add_recipe.html', auth.user)
def add_recipe():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if auth.get_user() is None:
        db.close()
        return redirect(auth.settings.LOGIN_URL)
    if request.method == 'POST':
        db.recipe.insert(
            name=request.forms.name,
            type=request.forms.type,
            description=request.forms.description,
            image=request.forms.image,
            author=auth.get_user()['id'],
            instruction_steps=request.forms.instruction_steps,
            servings=int(request.forms.servings),
            total_calories=0
        )
        recipe_id = db.executesql("SELECT last_insert_rowid()")[0][0]
        for img in request.forms.getlist('images[]'):
            db.recipe_image.insert(recipe_id=recipe_id, image=img)
        db.commit()
        db.close()
        return redirect(URL('index'))
    db.close()
    return dict()

@action('edit_recipe/<recipe_id:int>', method=['GET', 'POST'])
@action.uses('edit_recipe.html', auth.user)
def edit_recipe(recipe_id):
    db = DAL('sqlite://recipes_final.db', folder=None)
    recipe = db(db.recipe.id == recipe_id).select().first().as_dict()
    if recipe['author'] != auth.get_user()['id']:
        db.close()
        return "Unauthorized", 403
    if request.method == 'POST':
        db(db.recipe.id == recipe_id).update(
            name=request.forms.name,
            type=request.forms.type,
            description=request.forms.description,
            instruction_steps=request.forms.instruction_steps,
            servings=int(request.forms.servings)
        )
        db.commit()
        db.close()
        return redirect(URL('recipe', recipe_id=recipe_id))
    images = db(db.recipe_image.recipe_id == recipe_id).select(db.recipe_image.image).as_list()
    db.close()
    return dict(recipe=recipe, images=[img['image'] for img in images])

@action('ingredients')
@action.uses('ingredients.html', auth.user)
def ingredients_list():
    db = DAL('sqlite://recipes_final.db', folder=None)
    query = request.query.get('q', '')
    ingredients = db(db.ingredient.name.like(f'%{query}%')).select().as_list()
    db.close()
    return dict(ingredients=ingredients)

@action('add_ingredient', method=['GET', 'POST'])
@action.uses('add_ingredient.html', auth.user)
def add_ingredient():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if not auth.get_user().get('is_admin', False):
        db.close()
        return redirect(auth.settings.LOGIN_URL)
    if request.method == 'POST':
        db.ingredient.insert(
            name=request.forms.name,
            unit=request.forms.unit,
            calories_per_unit=float(request.forms.calories_per_unit),
            description=request.forms.description,
            image=request.forms.image or ''
        )
        db.commit()
        db.close()
        return redirect(URL('ingredients'))
    db.close()
    return dict()

@action('login', method=['GET', 'POST'])
@action.uses('login.html', auth.user)
def login():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if request.method == 'POST':
        user = db(db.auth_user.username == request.forms.username).select().first()
        if user and user.password == hashlib.sha256(request.forms.password.encode()).hexdigest():
            auth.login_user(user)
            db.close()
            return redirect(URL('index'))
        db.close()
        return "Invalid credentials", 401
    db.close()
    return dict()

@action('register', method=['GET', 'POST'])
@action.uses('register.html', auth.user)
def register():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if request.method == 'POST':
        try:
            db.auth_user.insert(
                username=request.forms.username,
                email=request.forms.email,
                password=hashlib.sha256(request.forms.password.encode()).hexdigest()
            )
            db.commit()
        except Exception:
            db.close()
            return "Username or email already exists", 400
        db.close()
        return redirect(URL('login'))
    db.close()
    return dict()

@action('profile')
@action.uses('profile.html', auth.user)
def profile():
    db = DAL('sqlite://recipes_final.db', folder=None)
    user = db(db.auth_user.id == auth.get_user()['id']).select().first().as_dict()
    db.close()
    return dict(user=user)

@action('edit_profile', method=['GET', 'POST'])
@action.uses('edit_profile.html', auth.user)
def edit_profile():
    db = DAL('sqlite://recipes_final.db', folder=None)
    user = db(db.auth_user.id == auth.get_user()['id']).select().first().as_dict()
    if request.method == 'POST':
        db(db.auth_user.id == auth.get_user()['id']).update(
            username=request.forms.username,
            email=request.forms.email
        )
        db.commit()
        db.close()
        return redirect(URL('profile'))
    db.close()
    return dict(user=user)

@action('search_by_ingredient', method=['GET', 'POST'])
@action.uses('search_by_ingredient.html', auth.user)
def search_by_ingredient():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if request.method == 'POST':
        ingredient_name = request.forms.ingredient_name
        recipes = db(db.recipe.id.belongs(
            db(db.recipe_ingredient.recipe_id == db.recipe.id)
            .join(db.ingredient)
            .select(db.recipe_ingredient.recipe_id)
            .where(db.ingredient.name.like(f'%{ingredient_name}%'))
        )).select().as_list()
        db.close()
        return dict(recipes=recipes)
    db.close()
    return dict()

@action('admin')
@action.uses('admin_panel.html', auth.user)
def admin_panel():
    db = DAL('sqlite://recipes_final.db', folder=None)
    if not auth.get_user().get('is_admin', False):
        db.close()
        return redirect(auth.settings.LOGIN_URL)
    users = db(db.auth_user).select().as_list()
    db.close()
    return dict(users=users)

@action('api/recipes')
@action.uses()
def api_recipes():
    db = DAL('sqlite://recipes_final.db', folder=None)
    search_query = request.query.get('q', '')
    recipes = db(db.recipe.name.like(f'%{search_query}%') | db.recipe.type.like(f'%{search_query}%')).select(
        db.recipe.id,
        db.recipe.name,
        db.recipe.type,
        db.recipe.description,
        db.recipe.total_calories
    ).as_list()
    db.close()
    return dict(recipes=recipes)

@action('api/ingredients')
@action.uses()
def api_ingredients():
    db = DAL('sqlite://recipes_final.db', folder=None)
    search_query = request.query.get('q', '')
    ingredients = db(db.ingredient.name.like(f'%{search_query}%')).select(
        db.ingredient.id,
        db.ingredient.name,
        db.ingredient.unit,
        db.ingredient.calories_per_unit,
        db.ingredient.description,
        db.ingredient.image
    ).as_list()
    db.close()
    return dict(ingredients=ingredients)

@action('scale_recipe/<recipe_id:int>/<factor:float>', method=['POST'])
@action.uses(auth.user)
def scale_recipe(recipe_id, factor):
    db = DAL('sqlite://recipes_final.db', folder=None)
    recipe = db(db.recipe.id == recipe_id).select().first()
    if not recipe or recipe.author != auth.get_user()['id']:
        db.close()
        return "Unauthorized", 403
    db(db.recipe.id == recipe_id).update(servings=recipe.servings * factor)
    for ri in db(db.recipe_ingredient.recipe_id == recipe_id).select():
        qty = ri.quantity_per_serving
        grams = ri.grams
        new_grams = measure_to_grams(qty, factor) if qty else None
        if new_grams:
            new_qty = f"{new_grams / UNIT_CONVERSIONS.get(ri.unit or 'g', 1)}{ri.unit or 'g'}"
        else:
            new_qty = qty
        db(db.recipe_ingredient.id == ri.id).update(
            quantity_per_serving=new_qty,
            grams=new_grams
        )
    db.commit()
    db.close()
    return redirect(URL('recipe', recipe_id=recipe_id))

@action('logout')
@action.uses(auth.user)
def logout():
    auth.logout_user()
    return redirect(URL('index'))