// Scenario presets for the demo control panel. Each one is sent to POST /api/evaluate.
// Dish ids come from the API menu (fixtures/menu.json).
export const SAMPLES = [
  {
    label: 'Happy path: simple mods',
    dishId: 'chicken_bowl', qty: 1, channel: 'doordash', customerName: 'Maria',
    text: 'no pickled onion, sauce on the side, extra avocado',
  },
  {
    label: 'Swap: pick a replacement',
    dishId: 'chicken_bowl', qty: 1, channel: 'ubereats', customerName: 'Ken',
    text: 'can you swap the chicken',
  },
  {
    label: 'Allergy: peanut (staff review)',
    dishId: 'satay_noodles', qty: 1, channel: 'doordash', customerName: 'Sam',
    text: 'satay noodles but no peanuts at all, I have a severe peanut allergy',
  },
  {
    label: 'Diet: vegetarian (staff review)',
    dishId: 'chicken_bowl', qty: 1, channel: 'walk-in', customerName: 'Priya',
    text: "I'm vegetarian",
  },
  {
    label: 'Ambiguous: "like last time"',
    dishId: 'falafel_wrap', qty: 1, channel: 'phone', customerName: 'Lee',
    text: 'make it like last time',
  },
]
