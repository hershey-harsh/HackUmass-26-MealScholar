from foodsmart.nutrients import per_100g


def top(food_db, query, hints=()):
    return food_db.search(query, hints=hints)[0].fdc_id


def test_generic_default_beats_shorter_but_more_specific_food(food_db):
    # bm25 alone ranks "Rice, white, cooked, glutinous" first; glutinous rice is 25% lower in kcal.
    assert top(food_db, "Rice, white, cooked", ("cooked",)) == 1


def test_hidden_fat_hint_selects_oil_version(food_db):
    assert top(food_db, "Rice, white, cooked", ("cooked", "oil")) == 3


def test_unrequested_negation_is_not_chosen(food_db):
    assert top(food_db, "Soft drink, cola") == 4


def test_main_food_named_first_beats_topping_and_brand_items(food_db):
    assert top(food_db, "Pizza, cheese") == 6


def test_baby_food_only_wins_when_nothing_else_fits(food_db):
    assert top(food_db, "Orange juice") == 12


def test_import_uses_atwater_energy_and_skips_foods_without_nutrients(food_db):
    broccoli = food_db.get(9)
    assert broccoli is not None and broccoli.per_100g["energy_kcal"] == 31  # specific (958) over general (957)
    assert food_db.get(8) is None  # "Milk, human" has no nutrient values


def test_unit_mismatch_is_ignored_not_converted():
    nutrients = per_100g([("328", "IU", 40.0), ("307", "MG", 5.0), ("418", "UG", 0.3)])
    assert "vitamin_d_ug" not in nutrients
    assert nutrients == {"sodium_mg": 5.0, "vitamin_b12_ug": 0.3}
