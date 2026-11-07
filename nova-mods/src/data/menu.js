// Fake restaurant data. Edit freely: the parser and substitution engine read from here.
// category = what role the ingredient plays (used to find swap candidates)
// taste    = tags used to score "how similar is this swap?"
// diet     = 'meat' blocks substitution for vegetarian customers
// prep     = seconds of extra prep (lower is better)

export const INGREDIENTS = {
  sesame_bun:   { id: 'sesame_bun',   name: 'Sesame bun',      category: 'bun',    allergens: ['gluten', 'sesame'], taste: ['soft', 'toasty'], cost: 0.5, prep: 20, aliases: ['bun', 'sesame bun'] },
  brioche_bun:  { id: 'brioche_bun',  name: 'Brioche bun',     category: 'bun',    allergens: ['gluten', 'egg', 'dairy'], taste: ['soft', 'toasty', 'sweet'], cost: 0.7, prep: 20, aliases: ['brioche', 'brioche bun'] },
  gf_bun:       { id: 'gf_bun',       name: 'Gluten-free bun', category: 'bun',    allergens: [], taste: ['soft'], cost: 1.2, prep: 30, aliases: ['gluten free bun', 'gf bun'] },

  beef_patty:    { id: 'beef_patty',    name: 'Beef patty',    category: 'protein', allergens: [], diet: ['meat'], taste: ['savory', 'grilled'], cost: 2.2, prep: 120, aliases: ['beef', 'patty', 'beef patty'] },
  chicken_patty: { id: 'chicken_patty', name: 'Crispy chicken', category: 'protein', allergens: ['gluten'], diet: ['meat'], taste: ['savory', 'crispy'], cost: 1.9, prep: 150, aliases: ['chicken', 'crispy chicken'] },
  veggie_patty:  { id: 'veggie_patty',  name: 'Veggie patty',  category: 'protein', allergens: ['soy'], taste: ['savory', 'grilled'], cost: 1.6, prep: 100, aliases: ['veggie', 'veggie patty'] },

  cheese:   { id: 'cheese',   name: 'Cheddar',  category: 'cheese',  allergens: ['dairy'], taste: ['savory', 'melty'], cost: 0.4, prep: 5,  aliases: ['cheese', 'cheddar'] },
  vegan_cheese: { id: 'vegan_cheese', name: 'Vegan cheese', category: 'cheese', allergens: [], taste: ['melty'], cost: 0.8, prep: 5, aliases: ['vegan cheese'] },

  lettuce:  { id: 'lettuce',  name: 'Lettuce',  category: 'veg', allergens: [], taste: ['fresh', 'crunchy'], cost: 0.15, prep: 5, aliases: ['lettuce'] },
  tomato:   { id: 'tomato',   name: 'Tomato',   category: 'veg', allergens: [], taste: ['fresh', 'juicy'],   cost: 0.2,  prep: 5, aliases: ['tomato', 'tomatoes'] },
  onion:    { id: 'onion',    name: 'Onion',    category: 'veg', allergens: [], taste: ['fresh', 'sharp', 'crunchy'], cost: 0.1, prep: 5, aliases: ['onion', 'onions'] },
  pickles:  { id: 'pickles',  name: 'Pickles',  category: 'veg', allergens: [], taste: ['tangy', 'crunchy'], cost: 0.1, prep: 2, aliases: ['pickle', 'pickles'] },
  avocado:  { id: 'avocado',  name: 'Avocado',  category: 'veg', allergens: [], taste: ['creamy', 'fresh'],  cost: 0.9, prep: 5, aliases: ['avocado', 'avo', 'guac'] },

  house_sauce: { id: 'house_sauce', name: 'House sauce', category: 'sauce', allergens: ['egg'], taste: ['creamy', 'tangy', 'savory'], cost: 0.2, prep: 2, aliases: ['sauce', 'house sauce', 'special sauce'] },
  garlic_mayo: { id: 'garlic_mayo', name: 'Garlic mayo', category: 'sauce', allergens: ['egg'], taste: ['creamy', 'garlic', 'savory'], cost: 0.25, prep: 2, aliases: ['mayo', 'garlic mayo'] },
  vegan_mayo:  { id: 'vegan_mayo',  name: 'Vegan mayo',  category: 'sauce', allergens: ['soy'], taste: ['creamy', 'tangy'], cost: 0.4, prep: 2, aliases: ['vegan mayo'] },
  bbq_sauce:   { id: 'bbq_sauce',   name: 'BBQ sauce',   category: 'sauce', allergens: [], taste: ['sweet', 'smoky'], cost: 0.2, prep: 2, aliases: ['bbq', 'bbq sauce'] },

  fries:      { id: 'fries',      name: 'Fries',       category: 'side', allergens: [], taste: ['crispy', 'salty'], cost: 0.5, prep: 180, aliases: ['fries', 'chips'] },
  sweet_fries:{ id: 'sweet_fries',name: 'Sweet potato fries', category: 'side', allergens: [], taste: ['crispy', 'sweet'], cost: 0.8, prep: 200, aliases: ['sweet potato fries', 'sweet fries'] },
  side_salad: { id: 'side_salad', name: 'Side salad',  category: 'side', allergens: [], taste: ['fresh', 'crunchy'], cost: 0.9, prep: 30, aliases: ['salad', 'side salad'] },
}

// Words a customer might use for an allergen -> canonical allergen name
export const ALLERGEN_WORDS = {
  sesame: 'sesame', egg: 'egg', eggs: 'egg', dairy: 'dairy', milk: 'dairy', lactose: 'dairy',
  gluten: 'gluten', wheat: 'gluten', soy: 'soy', peanut: 'peanut', peanuts: 'peanut',
  nut: 'nuts', nuts: 'nuts', shellfish: 'shellfish',
}

export const MENU = {
  classic_burger: {
    id: 'classic_burger', name: 'Classic Burger', price: 12.5,
    ingredients: ['sesame_bun', 'beef_patty', 'cheese', 'lettuce', 'tomato', 'onion', 'pickles', 'house_sauce', 'fries'],
  },
  chicken_sandwich: {
    id: 'chicken_sandwich', name: 'Crispy Chicken Sandwich', price: 13.0,
    ingredients: ['brioche_bun', 'chicken_patty', 'lettuce', 'pickles', 'garlic_mayo', 'fries'],
  },
  veggie_burger: {
    id: 'veggie_burger', name: 'Veggie Burger', price: 12.0,
    ingredients: ['sesame_bun', 'veggie_patty', 'avocado', 'lettuce', 'tomato', 'vegan_mayo', 'side_salad'],
  },
}

export const INITIAL_STOCK = Object.fromEntries(Object.keys(INGREDIENTS).map((id) => [id, 20]))
