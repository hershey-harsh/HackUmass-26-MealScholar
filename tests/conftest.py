import json
import zipfile
from pathlib import Path

import pytest

from foodsmart.nutrition import DATASETS, FoodDatabase, build_database


def food(fdc_id, description, nutrients, *, category=None, wweia=None):
    """FDC download-format food. nutrients: (number, unit, amount per 100 g)."""
    record = {
        "fdcId": fdc_id,
        "description": description,
        "foodNutrients": [
            {"nutrient": {"number": number, "unitName": unit}, "amount": amount} for number, unit, amount in nutrients
        ],
    }
    if wweia:
        record["wweiaFoodCategory"] = {"wweiaFoodCategoryDescription": wweia}
    if category:
        record["foodCategory"] = {"description": category}
    return record


def kcal_protein_fat(kcal, protein, fat, *extra):
    return [("208", "kcal", kcal), ("203", "g", protein), ("204", "g", fat), *extra]


FIXTURE_FOODS = {
    "Survey (FNDDS)": [
        food(1, "Rice, white, cooked, NS as to fat", kcal_protein_fat(129, 2.6, 1.3), wweia="Rice"),
        food(2, "Rice, white, cooked, glutinous", kcal_protein_fat(96, 2.0, 0.2), wweia="Rice"),
        food(3, "Rice, white, cooked, made with oil", kcal_protein_fat(151, 2.5, 3.4), wweia="Rice"),
        food(4, "Soft drink, cola", kcal_protein_fat(42, 0, 0), wweia="Soft drinks"),
        food(5, "Soft drink, cola, diet", kcal_protein_fat(2, 0, 0), wweia="Diet soft drinks"),
        food(
            6,
            "Pizza, cheese, from restaurant or fast food, thin crust",
            kcal_protein_fat(266, 11, 11, ("328", "µg", 0.2)),
            wweia="Pizza",
        ),
        food(7, "Topping from cheese pizza", kcal_protein_fat(230, 12, 16), wweia="Pizza"),
        food(8, "Milk, human", [], wweia="Human milk"),
        # FNDDS writes "Pizza with pepperoni", no comma after the main food.
        food(
            13,
            "Pizza with pepperoni, from restaurant or fast food, thin crust",
            kcal_protein_fat(282, 12, 13),
            wweia="Pizza",
        ),
        food(14, "Lemonade, fruit juice drink", kcal_protein_fat(46, 0, 0), wweia="Fruit drinks"),
        food(16, "Salad dressing, NFS, for salads", kcal_protein_fat(430, 0.9, 44), wweia="Salad dressing"),
    ],
    "Foundation": [
        # Many Foundation foods carry only Atwater energy (958/957), no 208.
        food(
            9,
            "Broccoli, raw",
            [("957", "kcal", 39), ("958", "kcal", 31), ("203", "g", 2.6), ("268", "kJ", 132)],
            category="Vegetables and Vegetable Products",
        ),
        food(
            17,
            "Mixed salad greens, raw",
            kcal_protein_fat(21, 1.6, 0.2),
            category="Vegetables and Vegetable Products",
        ),
    ],
    "SR Legacy": [
        food(10, 'PIZZA HUT 14" Cheese Pizza, Pan Crust', kcal_protein_fat(276, 11, 13), category="Fast Foods"),
        food(11, "Babyfood, juice, orange", kcal_protein_fat(45, 0.6, 0.3), category="Baby Foods"),
        food(12, "Orange juice, raw", kcal_protein_fat(45, 0.7, 0.2), category="Fruits and Fruit Juices"),
        food(15, "Lemonade, frozen concentrate, pink", kcal_protein_fat(192, 0.2, 0.7), category="Beverages"),
    ],
}


@pytest.fixture(scope="session")
def food_db_path(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fdc")
    downloads = root / "downloads"
    downloads.mkdir()
    for ds in DATASETS:
        with zipfile.ZipFile(downloads / ds.filename, "w") as zf:
            payload = {ds.root_key: FIXTURE_FOODS[ds.data_type]}
            zf.writestr(ds.filename.removesuffix(".zip") + ".json", json.dumps(payload))
    db_path = root / "fdc.sqlite"
    build_database(db_path, downloads, log=lambda _: None)
    return db_path


@pytest.fixture
def food_db(food_db_path) -> FoodDatabase:
    db = FoodDatabase(food_db_path)
    yield db
    db.close()
