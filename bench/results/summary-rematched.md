# Benchmark results, USDA lookup redone with current code (bench/report.py --rematch)

- kcal in band: 230/275 runs
- Runs with a powder/concentrate match: 4/275
- Pepperoni slice matched to a pepperoni pizza: 50/54 runs

### Speed and cost (per photo)

| Config | OK | p50 s | p90 s | Input tok | Image tok | Thinking tok | Output tok | $/photo | $/1k photos |
|---|---|---|---|---|---|---|---|---|---|
| `4-argon` low | 15/15 | 16.8 | 61.0 | 2394 | 1064 | 0 | 388 | 0.0087 | 8.66 |
| `4-argon` medium | 15/15 | 16.2 | 58.9 | 2394 | 1064 | 40 | 384 | 0.0090 | 9.02 |
| `4-hydrogen` low · res low | 15/15 | 5.5 | 8.7 | 1596 | 266 | 508 | 374 | 0.0090 | 9.01 |
| `4-hydrogen` low · res medium | 15/15 | 6.3 | 8.2 | 1870 | 540 | 609 | 359 | 0.0101 | 10.07 |
| `4-hydrogen` low | 15/15 | 5.9 | 9.3 | 2394 | 1064 | 435 | 375 | 0.0097 | 9.67 |
| `4-hydrogen` low · prompt 0e460f6e | 15/15 | 5.4 | 14.4 | 2514 | 1064 | 488 | 360 | 0.0101 | 10.13 |
| `4-hydrogen` low · prompt 56e180c6 | 15/15 | 6.1 | 8.5 | 2394 | 1064 | 517 | 358 | 0.0102 | 10.15 |
| `4-hydrogen` low · prompt e9181571 (current) | 15/15 | 5.4 | 7.5 | 2477 | 1064 | 478 | 368 | 0.0101 | 10.06 |
| `4-hydrogen` low · res ultra_high | 15/15 | 5.9 | 9.7 | 3490 | 2160 | 447 | 378 | 0.0114 | 11.43 |
| `4-hydrogen` medium | 15/15 | 8.4 | 12.9 | 2394 | 1064 | 1052 | 353 | 0.0141 | 14.12 |
| `4-hydrogen` high | 15/15 | 8.7 | 16.9 | 2394 | 1064 | 1195 | 355 | 0.0152 | 15.22 |
| `4-hydrogen` xhigh | 5/5 | 99.1 | 142.6 | 2394 | 1064 | 18394 | 302 | 0.1438 | 143.81 |
| `3.8-flash` low | 15/15 | 7.0 | 10.1 | 1523 | 1064 | 0 | 421 | 0.0027 | 2.72 |
| `3.8-flash` medium | 15/15 | 11.8 | 16.3 | 1523 | 1064 | 709 | 358 | 0.0051 | 5.15 |
| `3.6-flash` minimal | 15/15 | 4.1 | 5.7 | 1523 | 1064 | 0 | 275 | 0.0022 | 2.17 |
| `3.6-flash` medium | 15/15 | 9.7 | 13.0 | 1523 | 1064 | 838 | 305 | 0.0054 | 5.43 |
| `3.5-flash-lite` minimal · res low | 15/15 | 3.0 | 4.2 | 725 | 266 | 0 | 318 | 0.0010 | 1.01 |
| `3.5-flash-lite` minimal · res medium | 15/15 | 3.2 | 4.0 | 999 | 540 | 0 | 314 | 0.0011 | 1.08 |
| `3.5-flash-lite` minimal | 15/15 | 3.0 | 4.4 | 1523 | 1064 | 0 | 327 | 0.0013 | 1.28 |
| `3.5-flash-lite` minimal · res ultra_high | 15/15 | 3.4 | 5.0 | 2619 | 2160 | 0 | 354 | 0.0017 | 1.67 |
| `3.5-flash-lite` low | 15/15 | 3.1 | 4.2 | 1523 | 1064 | 0 | 317 | 0.0012 | 1.25 |
| `3.1-flash-lite` minimal | 15/15 | 3.3 | 6.1 | 1523 | 1064 | 0 | 377 | 0.0009 | 0.95 |

### What Gemini saw (identification, portions, boxes)

| Config | Recall | Ginger ale exact / soda | Pink lemonade exact / lemonade | Honey mustard | Unexpected items/photo | Grams in band | Median grams error | Egg g ok | Dressing g ok | Useful question | Box IoU | Grams CV |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `4-argon` low | 100% | 100% / 100% | 0% / 100% | 100% | 0.00 | 100% | 11% | 50% | 100% | 100% | 0.97 | 2% |
| `4-argon` medium | 99% | 67% / 67% | 0% / 100% | 100% | 0.00 | 93% | 12% | 67% | 83% | 100% | 0.98 | 7% |
| `4-hydrogen` low · res low | 100% | 67% / 67% | 67% / 67% | 100% | 0.00 | 87% | 6% | 33% | 100% | 83% | 0.95 | 9% |
| `4-hydrogen` low · res medium | 95% | 33% / 33% | 100% / 100% | 100% | 0.00 | 80% | 11% | 50% | 67% | 100% | 0.97 | 3% |
| `4-hydrogen` low | 97% | 67% / 67% | 100% / 100% | 100% | 0.00 | 100% | 8% | 100% | 83% | 100% | 0.96 | 7% |
| `4-hydrogen` low · prompt 0e460f6e | 95% | 67% / 67% | 100% / 100% | 100% | 0.00 | 100% | 7% | 100% | 67% | 92% | 0.97 | 5% |
| `4-hydrogen` low · prompt 56e180c6 | 95% | 100% / 100% | 100% / 100% | 100% | 0.00 | 100% | 7% | 100% | 67% | 92% | 0.97 | 6% |
| `4-hydrogen` low · prompt e9181571 (current) | 100% | 67% / 67% | 100% / 100% | 100% | 0.00 | 100% | 4% | 83% | 100% | 100% | 0.96 | 5% |
| `4-hydrogen` low · res ultra_high | 97% | 67% / 67% | 100% / 100% | 100% | 0.00 | 100% | 4% | 100% | 83% | 100% | 0.98 | 3% |
| `4-hydrogen` medium | 97% | 100% / 100% | 100% / 100% | 100% | 0.00 | 100% | 6% | 100% | 83% | 83% | 0.97 | 4% |
| `4-hydrogen` high | 100% | 100% / 100% | 100% / 100% | 100% | 0.00 | 100% | 7% | 83% | 100% | 75% | 0.97 | 4% |
| `4-hydrogen` xhigh | 100% | 100% / 100% | 100% / 100% | 100% | 0.00 | 100% | 11% | 100% | 100% | 75% | 0.97 | – |
| `3.8-flash` low | 100% | 0% / 0% | 100% / 100% | 100% | 0.00 | 80% | 6% | 100% | 100% | 100% | 0.97 | 2% |
| `3.8-flash` medium | 99% | 0% / 0% | 100% / 100% | 100% | 0.00 | 73% | 17% | 100% | 100% | 75% | 0.95 | 8% |
| `3.6-flash` minimal | 100% | 0% / 0% | 100% / 100% | 100% | 0.00 | 93% | 12% | 100% | 100% | 100% | 0.97 | 6% |
| `3.6-flash` medium | 100% | 0% / 0% | 100% / 100% | 100% | 0.00 | 73% | 17% | 50% | 100% | 75% | 0.96 | 10% |
| `3.5-flash-lite` minimal · res low | 81% | 0% / 33% | 100% / 100% | 50% | 0.00 | 73% | 17% | 50% | 50% | 67% | 0.97 | 23% |
| `3.5-flash-lite` minimal · res medium | 79% | 0% / 0% | 67% / 67% | 17% | 0.00 | 80% | 4% | 50% | 17% | 75% | 0.97 | 11% |
| `3.5-flash-lite` minimal | 83% | 0% / 0% | 0% / 0% | 50% | 0.00 | 87% | 17% | 50% | 33% | 100% | 0.97 | 5% |
| `3.5-flash-lite` minimal · res ultra_high | 87% | 0% / 67% | 100% / 100% | 83% | 0.00 | 87% | 11% | 50% | 33% | 83% | 0.96 | 6% |
| `3.5-flash-lite` low | 81% | 0% / 0% | 100% / 100% | 50% | 0.00 | 87% | 17% | 50% | 33% | 75% | 0.97 | 5% |
| `3.1-flash-lite` minimal | 100% | 0% / 0% | 33% / 33% | 0% | 0.00 | 60% | 17% | 0% | 100% | 100% | 0.95 | 4% |

### End to end with the USDA lookup

| Config | Items matched | Photos with a powder/concentrate match | Pepperoni matched to pepperoni pizza | kcal in band | Median kcal error | Same salad, far/near grams | Edge items logged |
|---|---|---|---|---|---|---|---|
| `4-argon` low | 100% | 0% | 100% | 100% | 6% | 0.77 | 20% |
| `4-argon` medium | 100% | 0% | 67% | 93% | 18% | 0.78 | 40% |
| `4-hydrogen` low · res low | 100% | 0% | 100% | 80% | 7% | 1.02 | 40% |
| `4-hydrogen` low · res medium | 100% | 7% | 100% | 87% | 12% | 0.67 | 20% |
| `4-hydrogen` low | 100% | 0% | 100% | 93% | 10% | 0.88 | 27% |
| `4-hydrogen` low · prompt 0e460f6e | 100% | 13% | 100% | 87% | 9% | 0.86 | 33% |
| `4-hydrogen` low · prompt 56e180c6 | 100% | 0% | 100% | 93% | 8% | 0.93 | 27% |
| `4-hydrogen` low · prompt e9181571 (current) | 100% | 0% | 100% | 87% | 7% | 0.81 | 27% |
| `4-hydrogen` low · res ultra_high | 100% | 0% | 100% | 93% | 7% | 0.90 | 33% |
| `4-hydrogen` medium | 100% | 0% | 100% | 100% | 6% | 0.95 | 33% |
| `4-hydrogen` high | 100% | 7% | 100% | 93% | 10% | 0.94 | 40% |
| `4-hydrogen` xhigh | 100% | 0% | 100% | 100% | 13% | 0.90 | 40% |
| `3.8-flash` low | 100% | 0% | 33% | 87% | 15% | 0.94 | 40% |
| `3.8-flash` medium | 100% | 0% | 67% | 67% | 15% | 0.81 | 40% |
| `3.6-flash` minimal | 100% | 0% | 100% | 100% | 10% | 0.89 | 27% |
| `3.6-flash` medium | 100% | 0% | 100% | 73% | 13% | 0.78 | 40% |
| `3.5-flash-lite` minimal · res low | 100% | 7% | 100% | 73% | 15% | 0.75 | 40% |
| `3.5-flash-lite` minimal · res medium | 100% | 7% | 100% | 73% | 19% | 0.71 | 40% |
| `3.5-flash-lite` minimal | 100% | 0% | 100% | 60% | 32% | 0.83 | 40% |
| `3.5-flash-lite` minimal · res ultra_high | 100% | 0% | 100% | 93% | 5% | 0.83 | 33% |
| `3.5-flash-lite` low | 100% | 0% | 100% | 80% | 18% | 0.77 | 40% |
| `3.1-flash-lite` minimal | 100% | 0% | 100% | 53% | 28% | 0.91 | 20% |

### Range width and coverage

Item range: how far each food's low and high grams sit from likely (median). kcal range: high minus
low over likely for the labeled items (median), for photos of one food and of several, combined as
`compute.add_ranges` does and with every low and every high added up. Covers: the combined range
contains the reference amount.

| Config | Item range below / above likely | kcal range, one food | kcal range, several foods | Several foods, lows and highs added | Covers reference grams | Covers reference kcal |
|---|---|---|---|---|---|---|
| `4-argon` low | −38% / +40% | 50% | 53% | 71% | 100% | 80% |
| `4-argon` medium | −30% / +36% | 53% | 37% | 46% | 87% | 60% |
| `4-hydrogen` low · res low | −25% / +40% | 58% | 44% | 60% | 87% | 80% |
| `4-hydrogen` low · res medium | −29% / +38% | 68% | 39% | 51% | 87% | 80% |
| `4-hydrogen` low | −29% / +33% | 56% | 38% | 51% | 100% | 93% |
| `4-hydrogen` low · prompt 0e460f6e | −32% / +40% | 68% | 40% | 53% | 100% | 87% |
| `4-hydrogen` low · prompt 56e180c6 | −30% / +38% | 65% | 39% | 54% | 100% | 87% |
| `4-hydrogen` low · prompt e9181571 (current) | −29% / +36% | 58% | 39% | 51% | 100% | 87% |
| `4-hydrogen` low · res ultra_high | −29% / +36% | 58% | 38% | 49% | 100% | 93% |
| `4-hydrogen` medium | −33% / +36% | 68% | 38% | 52% | 100% | 100% |
| `4-hydrogen` high | −33% / +38% | 60% | 46% | 63% | 100% | 93% |
| `4-hydrogen` xhigh | −30% / +44% | 74% | 50% | 62% | 80% | 80% |
| `3.8-flash` low | −33% / +43% | 50% | 45% | 61% | 60% | 60% |
| `3.8-flash` medium | −33% / +43% | 55% | 48% | 67% | 67% | 60% |
| `3.6-flash` minimal | −33% / +39% | 62% | 45% | 60% | 93% | 87% |
| `3.6-flash` medium | −33% / +38% | 68% | 48% | 65% | 67% | 67% |
| `3.5-flash-lite` minimal · res low | −25% / +25% | 50% | 41% | 56% | 60% | 60% |
| `3.5-flash-lite` minimal · res medium | −25% / +25% | 50% | 41% | 57% | 73% | 60% |
| `3.5-flash-lite` minimal | −25% / +25% | 50% | 43% | 58% | 73% | 33% |
| `3.5-flash-lite` minimal · res ultra_high | −32% / +36% | 50% | 44% | 58% | 87% | 87% |
| `3.5-flash-lite` low | −25% / +25% | 50% | 50% | 67% | 60% | 47% |
| `3.1-flash-lite` minimal | −33% / +33% | 50% | 39% | 50% | 60% | 40% |

### What each config called the drinks and the dressing (all reps)

| Config | IMG_0580 (ginger ale) | IMG_0581 (pink lemonade) | IMG_0582+0583 dressing |
|---|---|---|---|
| `4-argon` low | soft drink, ginger ale ×3 | lemonade, fruit flavored drink ×3 | salad dressing, honey mustard, regular ×5, salad dressing, honey mustard dressing, regular ×1 |
| `4-argon` medium | soft drink, ginger ale ×2, alcoholic beverage, whiskey, ginger ale ×1 | lemonade, fruit-flavored drink ×2, lemonade, fruit flavored drink ×1 | salad dressing, honey mustard, regular ×4, salad dressing, honey mustard ×1, salad dressing, honey mustard dressing, regular ×1, lettuce, salad, with assorted vegetables, no dressing ×1 |
| `4-hydrogen` low · res low | soft drink, ginger ale ×2, tea, iced, unsweetened ×1 | lemonade, pink ×1, fruit juice drink, with ice ×1, fruit juice drink, pink lemonade ×1 | salad dressing, honey mustard ×5, salad dressing, honey mustard, regular ×1 |
| `4-hydrogen` low · res medium | tea, iced, sweetened ×1, tea, iced, brewed, black, unsweetened ×1, soft drink, ginger ale ×1 | lemonade, pink ×2, lemonade, pink, prepared ×1 | salad dressing, honey mustard ×6, salad, arugula, with red onion, no dressing ×1, salad, vegetable, tossed, without dressing ×1 |
| `4-hydrogen` low | soft drink, ginger ale ×2, tea, iced, brewed, black, unsweetened ×1 | lemonade, pink ×3 | salad dressing, honey mustard ×5, salad dressing, honey mustard, regular ×1, salad, mixed greens, with red onion, no dressing ×1 |
| `4-hydrogen` low · prompt 0e460f6e | soft drink, ginger ale ×2, tea, iced, sweetened ×1 | lemonade, pink, prepared from frozen concentrate ×1, lemonade, pink ×1, lemonade, pink, prepared ×1 | salad dressing, honey mustard ×5, salad dressing, honey mustard, regular ×1, salad, mixed greens with red onion, no dressing ×1, salad, green, with red onion, no dressing ×1 |
| `4-hydrogen` low · prompt 56e180c6 | soft drink, ginger ale ×3 | lemonade, pink ×2, fruit juice drink, pink lemonade, with ice ×1 | salad dressing, honey mustard ×6, salad, green, with red onion, no dressing ×2 |
| `4-hydrogen` low · prompt e9181571 (current) | soft drink, ginger ale ×2, tea, iced, brewed, unsweetened ×1 | lemonade, pink ×2, fruit juice drink, pink lemonade ×1 | salad dressing, honey mustard ×6 |
| `4-hydrogen` low · res ultra_high | soft drink, ginger ale ×2, tea, iced, brewed, sweetened ×1 | lemonade, pink ×2, fruit juice drink, pink lemonade ×1 | salad dressing, honey mustard ×4, salad dressing, honey mustard, regular ×2, salad, green, with red onion, no dressing ×1 |
| `4-hydrogen` medium | soft drink, ginger ale ×3 | lemonade, pink ×3 | salad dressing, honey mustard, regular ×3, salad dressing, honey mustard ×3, arugula, raw, with red onion, no dressing ×1 |
| `4-hydrogen` high | soft drink, ginger ale ×3 | lemonade, pink ×2, lemonade, pink, prepared from powder or concentrate ×1 | salad dressing, honey mustard, regular ×4, salad dressing, honey mustard ×2 |
| `4-hydrogen` xhigh | soft drink, ginger ale ×1 | lemonade, pink, ready-to-drink ×1 | salad dressing, honey mustard ×2 |
| `3.8-flash` low | tea, iced, brewed, sweetened ×2, tea, brewed, sweetened with sugar ×1 | lemonade, pink ×3 | salad dressing, honey mustard ×5, salad dressing, honey mustard, regular ×1 |
| `3.8-flash` medium | tea, iced, brewed ×2, tea, iced, brewed, sweetened ×1 | lemonade, pink ×3 | salad dressing, honey mustard ×5, salad dressing, honey mustard, regular ×1 |
| `3.6-flash` minimal | soft drink, cola, or iced tea or alcoholic beverage ×1, tea, sweet, brewed, sweetened with sugar ×1, tea, iced, sweetened ×1 | lemonade, pink ×2, fruit punch drink, or lemonade, pink ×1 | salad dressing, honey mustard ×5, salad dressing, honey mustard or mustard mayonnaise based ×1 |
| `3.6-flash` medium | tea, sweet, iced ×1, tea, sweet, brewed, with ice ×1, tea, iced, sweetened ×1 | lemonade, pink ×2, lemonade, pink, ready-to-drink ×1 | salad dressing, honey mustard ×6 |
| `3.5-flash-lite` minimal · res low | alcoholic beverage, whiskey ×2, soft drink, cola ×1 | lemonade, pink ×2, beverage, lemonade, pink ×1 | salad dressing, honey mustard, dressing ×1, salad dressing, honey mustard ×1, salad dressing, honey mustard, commercial ×1, salad, vegetable, with dressing ×1 |
| `3.5-flash-lite` minimal · res medium | beverage, iced tea, sweetened ×2, beverages, tea, iced, sweetened ×1 | beverage, lemonade, pink ×1, fruit juice, grapefruit, sweetened ×1, lemonade, pink ×1 | salad, greens, with lettuce and other vegetables, without dressing ×2, salad, vegetable, with dressing ×2, salad dressing, creamy dressing ×1, salad dressing, creamy ×1 |
| `3.5-flash-lite` minimal | beverage, iced tea, sweetened ×3 | cocktail ×1, cocktail, alcoholic beverage ×1, cocktail, alcoholic ×1 | salad, vegetable, with cheese and dressing ×2, salad dressing, honey mustard, commercial ×1, salad, greens, with lettuce and other vegetables, without dressing ×1, salad dressing, honey mustard, creamy ×1 |
| `3.5-flash-lite` minimal · res ultra_high | soft drink, cola, regular ×1, soft drink, cola ×1, tea, iced, brewed, sweetened ×1 | lemonade, pink ×3 | salad, vegetable, other than lettuce or tomato, without dressing ×2, salad dressing, honey mustard, regular ×2, salad dressing, honey mustard, commercial ×1, salad, greens, with other vegetables, without dressing ×1 |
| `3.5-flash-lite` low | beverage, iced tea, sweetened ×2, alcoholic beverage, whiskey ×1 | lemonade, pink ×3 | salad dressing, honey mustard, commercial ×3, salad, vegetable, with cheese and dressing ×2, salad, vegetable, greens, with other vegetables, without dressing ×1, salad, vegetable, with cheese and egg ×1 |
| `3.1-flash-lite` minimal | tea, sweetened, iced ×2, tea, sweetened, ready to drink ×1 | fruit juice drink, pink lemonade ×1, fruit juice, grapefruit, pink or red ×1, fruit juice drink ×1 | salad dressing, creamy ×4, salad dressing, mayonnaise based, regular ×1, salad dressing, creamy, other ×1 |

### Unexpected items (not in the photo's labels or allowed edge items)


### USDA lookup: every fdc_query Gemini produced

| fdc_query from Gemini | USDA match | kcal/100 g | Times |
|---|---|---|---|
| Pizza, cheese, from restaurant or fast food, thin crust | Pizza, cheese, from restaurant or fast food, thin crust | 266 | 84 |
| Salad dressing, honey mustard | Salad dressing, honey mustard, regular | 464 | 67 |
| Cheese, cheddar, shredded | Cheese, Cheddar | 409 | 52 |
| Egg, whole, boiled | Egg, whole, cooked, hard-boiled | 155 | 48 |
| Cheese, cheddar | Cheese, Cheddar | 409 | 45 |
| Lemonade, pink | Lemonade, fruit juice drink | 46 | 37 |
| Onions, red, raw | Onions, red, raw | 44 | 37 |
| Pizza, pepperoni, from restaurant or fast food, thin crust | Pizza with pepperoni, from restaurant or fast food, thin crust | 282 | 36 |
| Arugula, raw | Arugula, raw | 25 | 35 |
| Soft drink, ginger ale | Soft drink, ginger ale | 34 | 30 |
| Egg, whole, boiled or poached | Egg, whole, boiled or poached | 143 | 29 |
| Salad dressing, honey mustard, regular | Salad dressing, honey mustard, regular | 464 | 26 |
| Egg, whole, cooked, hard-boiled | Egg, whole, cooked, hard-boiled | 155 | 21 |
| Onions, raw | Onions, raw | 38 | 19 |
| Pizza, cheese, from restaurant or fast food, regular crust | Pizza, cheese, from restaurant or fast food, NS as to type of crust | 266 | 18 |
| Water, tap | Water, tap | 0 | 17 |
| Pizza, cheese, thin crust | Pizza, extra cheese, thin crust | 268 | 15 |
| Egg, whole, hard-boiled | Egg, whole, cooked, hard-boiled | 155 | 14 |
| Beverage, iced tea, sweetened | Beverages, tea, green, ready-to-drink, sweetened | 27 | 9 |
| Lettuce, salad greens, raw | Lettuce, leaf, green, raw | 18 | 8 |
| Pizza, pepperoni, from restaurant or fast food, regular crust | Pizza with pepperoni, from restaurant or fast food, NS as to type of crust | 282 | 8 |
| Cheese, Cheddar | Cheese, Cheddar | 409 | 7 |
| Lettuce, arugula, raw | Lettuce, arugula, raw | 25 | 7 |
| Pizza, cheese, from restaurant or fast food | Pizza, cheese, from restaurant or fast food, NS as to type of crust | 266 | 7 |
| Pizza, pepperoni, thin crust | Pizza with pepperoni, from frozen, thin crust | 274 | 7 |
| Salad dressing, honey mustard, commercial | Salad dressing, honey mustard, regular | 464 | 7 |
| Tea, iced, sweetened | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 7 |
| Arugula, raw, with red onion | Arugula, raw | 25 | 6 |
| Tea, iced, brewed, sweetened | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 6 |
| Salad dressing, creamy | Salad dressing, poppyseed, creamy | 399 | 5 |
| Salad greens, raw | Mixed salad greens, raw | 21 | 5 |
| Salad, vegetable, with cheese and dressing | Salad dressing, NFS, for salads | 430 | 5 |
| Fruit juice drink, pink lemonade | Fruit juice drink | 10 | 4 |
| Lemonade, fruit flavored drink | Lemonade, fruit flavored drink | 25 | 4 |
| Lettuce, mixed greens, raw | Lettuce, leaf, green, raw | 18 | 4 |
| Lettuce, salad, mixed greens, raw | Lettuce, leaf, green, raw | 18 | 4 |
| Pizza, with pepperoni, from restaurant or fast food, thin crust | Pizza with pepperoni, from restaurant or fast food, thin crust | 282 | 4 |
| Salad, green, with red onion, no dressing | Cobb salad, no dressing | 93 | 4 |
| Alcoholic beverage, whiskey | Alcoholic beverage, whiskey sour | 149 | 3 |
| Cheese, Mexican blend | Cheese, Mexican blend | 362 | 3 |
| Onion, red, raw | Onions, red, raw | 44 | 3 |
| Pizza, cheese and pepperoni, thin crust | Pizza, extra cheese, thin crust | 268 | 3 |
| Pizza, cheese, regular crust | Pizza, cheese, stuffed crust | 274 | 3 |
| Salad greens, mixed, raw | Mixed salad greens, raw | 21 | 3 |
| Salad, greens, with lettuce and other vegetables, without dressing | Lettuce, salad with egg, tomato, and/or carrots, with or without other vegetables, no dressing | 50 | 3 |
| Salad, mixed greens, raw | Mixed salad greens, raw | 21 | 3 |
| Salad, vegetable, with dressing | Salad dressing, NFS, for salads | 430 | 3 |
| Tea, iced, brewed | Tea, iced, brewed, black, unsweetened | 1 | 3 |
| Tea, iced, brewed, unsweetened | Tea, iced, brewed, black, unsweetened | 1 | 3 |
| Alcoholic beverage, wine, table, white | Alcoholic beverage, wine, table, white | 82 | 2 |
| Beer, light | Beer, light | 29 | 2 |
| Beverage, lemonade, pink | Beverages, lemonade, frozen concentrate, pink, prepared with water ⚠ | 43 | 2 |
| Cheese, Cheddar or Colby, shredded | Cheese, Colby | 394 | 2 |
| Egg, whole, boiled, hard | Egg, whole, cooked, hard-boiled | 155 | 2 |
| Lemonade, fruit-flavored drink | Lemonade, fruit flavored drink | 25 | 2 |
| Lemonade, pink, prepared | Lemonade, powder, prepared with water ⚠ | 14 | 2 |
| Lemonade, pink, ready-to-drink | Lemonade, fruit juice drink | 46 | 2 |
| Lettuce, raw | Lettuce, raw | 20 | 2 |
| Lettuce, salad, with assorted vegetables, raw | Lettuce, raw | 20 | 2 |
| Onion, raw | Onions, raw | 38 | 2 |
| Pizza, pepperoni, from restaurant or fast food | Pizza with pepperoni, from restaurant or fast food, NS as to type of crust | 282 | 2 |
| Salad dressing, honey mustard dressing, regular | Salad dressing, honey mustard, regular | 464 | 2 |
| Salad dressing, honey mustard, dressing | Salad dressing, honey mustard, regular | 464 | 2 |
| Salad, mixed greens, raw, with red onion | Mixed salad greens, raw | 21 | 2 |
| Salad, vegetable, lettuce and other greens, with red onion | Mixed salad greens, raw | 21 | 2 |
| Salad, vegetable, other than lettuce or tomato, without dressing | Lettuce, salad with egg, tomato, and/or carrots, with or without other vegetables, no dressing | 50 | 2 |
| Soft drink, cola | Soft drink, cola | 42 | 2 |
| Tea, iced, brewed, black, unsweetened | Tea, iced, brewed, black, unsweetened | 1 | 2 |
| Tea, iced, unsweetened | Tea, iced, brewed, black, unsweetened | 1 | 2 |
| Tea, sweetened, iced | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 2 |
| Water, municipal | Water, NFS | 0 | 2 |
| Alcoholic beverage, beer, regular, all | Alcoholic beverage, beer, regular, all | 43 | 1 |
| Alcoholic beverage, whiskey, ginger ale | Alcoholic beverage, whiskey sour | 149 | 1 |
| Arugula, raw, with red onion, no dressing | Arugula, raw | 25 | 1 |
| Beer, regular | Beer | 43 | 1 |
| Beverages, tea, iced, sweetened | Beverages, tea, green, ready-to-drink, sweetened | 27 | 1 |
| Cheese, Cheddar or American type | Cheese, american cheddar, imitation | 239 | 1 |
| Cheese, Colby Jack, shredded | Cheese, Colby Jack | 393 | 1 |
| Cheese, cheddar or Colby, shredded | Cheese, Colby | 394 | 1 |
| Cocktail | Cocktail, NFS | 89 | 1 |
| Cocktail, alcoholic | Cocktail, NFS | 89 | 1 |
| Cocktail, alcoholic beverage | Cocktail, NFS | 89 | 1 |
| Egg salad | Egg salad, made with mayonnaise | 257 | 1 |
| Egg, hard-boiled | Egg, whole, cooked, hard-boiled | 155 | 1 |
| Egg, whole, boiled, hard-cooked | Egg, whole, cooked, hard-boiled | 155 | 1 |
| Fruit juice drink | Fruit juice drink | 10 | 1 |
| Fruit juice drink, pink lemonade, with ice | Fruit juice drink | 10 | 1 |
| Fruit juice drink, with ice | Fruit juice drink | 10 | 1 |
| Fruit juice, grapefruit, pink or red | Fruit juice, NFS | 51 | 1 |
| Fruit juice, grapefruit, sweetened | Fruit juice, NFS | 51 | 1 |
| Fruit punch drink, or lemonade, pink | Beverages, Fruit punch drink, with added nutrients, canned | 47 | 1 |
| Iced tea, sweetened | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 1 |
| Lemonade, pink, prepared from frozen concentrate | Lemonade, frozen concentrate, pink ⚠ | 192 | 1 |
| Lemonade, pink, prepared from powder or concentrate | Lemonade, frozen concentrate, pink ⚠ | 192 | 1 |
| Lettuce, mixed greens | Lettuce, leaf, green, raw | 18 | 1 |
| Lettuce, mixed greens, raw, with cheese and red onion | Lettuce, leaf, red, raw | 15 | 1 |
| Lettuce, mixed greens, with red onion, raw | Lettuce, leaf, red, raw | 15 | 1 |
| Lettuce, raw, mixed greens | Lettuce, leaf, green, raw | 18 | 1 |
| Lettuce, salad greens, mixed varieties, raw | Lettuce, leaf, green, raw | 18 | 1 |
| Lettuce, salad, with assorted vegetables, no dressing | Lettuce, salad with assorted vegetables excluding tomatoes and carrots, no dressing | 23 | 1 |
| Pizza crust, baked | Pizza, cheese, stuffed crust | 274 | 1 |
| Pizza crust, plain | Pizza, cheese, stuffed crust | 274 | 1 |
| Pizza, cheese, from restaurant or fast food, medium crust | Pizza, cheese, from restaurant or fast food, medium crust | 266 | 1 |
| Pizza, cheese, regular crust, baked | Pizza, cheese, stuffed crust | 274 | 1 |
| Pizza, pepperoni, regular crust, baked | Pizza with pepperoni, stuffed crust | 296 | 1 |
| Pizza, with meat, from restaurant or fast food, thin crust | Pizza with meat and vegetables, from restaurant or fast food, thin crust | 241 | 1 |
| Pizza, with pepperoni, from restaurant or fast food, medium crust | Pizza with pepperoni, from restaurant or fast food,  medium crust | 282 | 1 |
| Salad dressing, creamy dressing | Salad dressing, poppyseed, creamy | 399 | 1 |
| Salad dressing, creamy, other | Salad dressing, poppyseed, creamy | 399 | 1 |
| Salad dressing, honey mustard or mustard mayonnaise based | Salad dressing, honey mustard, regular | 464 | 1 |
| Salad dressing, honey mustard, creamy | Salad dressing, honey mustard, regular | 464 | 1 |
| Salad dressing, honey mustard, low calorie | Salad dressing, honey mustard dressing, reduced calorie | 207 | 1 |
| Salad dressing, mayonnaise based, regular | Salad dressing, mayonnaise, regular | 680 | 1 |
| Salad greens, arugula and lettuce, raw | Mixed salad greens, raw | 21 | 1 |
| Salad greens, raw, with onions | Mixed salad greens, raw | 21 | 1 |
| Salad, arugula and red onion, raw | Carrots, raw, salad | 212 | 1 |
| Salad, arugula, with red onion, no dressing | Cobb salad, no dressing | 93 | 1 |
| Salad, greens, mixed, raw | Mixed salad greens, raw | 21 | 1 |
| Salad, greens, mixed, with cheese | Mixed salad greens, raw | 21 | 1 |
| Salad, greens, raw, plain | Mixed salad greens, raw | 21 | 1 |
| Salad, greens, raw, with onions | Mixed salad greens, raw | 21 | 1 |
| Salad, greens, with onion | Mixed salad greens, raw | 21 | 1 |
| Salad, greens, with other vegetables, without dressing | Lettuce, salad with egg, tomato, and/or carrots, with or without other vegetables, no dressing | 50 | 1 |
| Salad, greens, with vegetables | Mixed salad greens, raw | 21 | 1 |
| Salad, mixed greens with red onion, no dressing | Mixed salad greens, raw | 21 | 1 |
| Salad, mixed greens, with red onion, no dressing | Mixed salad greens, raw | 21 | 1 |
| Salad, vegetable, arugula, raw | Gelatin salad with vegetables | 61 | 1 |
| Salad, vegetable, greens, with other vegetables, without dressing | Lettuce, salad with egg, tomato, and/or carrots, with or without other vegetables, no dressing | 50 | 1 |
| Salad, vegetable, tossed, without dressing | Salad dressing, NFS, for salads | 430 | 1 |
| Salad, vegetable, with cheese and egg | Tuna salad with egg | 190 | 1 |
| Salad, vegetable, with cheese and eggs | Tuna salad with egg | 190 | 1 |
| Salad, vegetable, with low calorie dressing | Salad dressing, russian dressing, low calorie | 141 | 1 |
| Salad, vegetable, with oil and vinegar dressing | Salad dressing, home recipe, vinegar and oil | 449 | 1 |
| Soft drink, cola, or iced tea or alcoholic beverage | Soft drink, cola | 42 | 1 |
| Soft drink, cola, regular | Soft drink, cola | 42 | 1 |
| Tea, brewed, sweetened with sugar | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 1 |
| Tea, iced, un-sweetened | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 1 |
| Tea, sweet, brewed, sweetened with sugar | Tea, iced, brewed, black, pre-sweetened with sugar | 32 | 1 |
| Tea, sweet, brewed, with ice | Tea, ginger | 1 | 1 |
| Tea, sweet, iced | Tea, iced, bottled, black | 45 | 1 |
| Tea, sweetened, ready to drink | Tea, ginger | 1 | 1 |
| Water, tap or Soft drink | Water, tap | 0 | 1 |
| Water, tap or alcoholic beverage | Water, tap | 0 | 1 |
