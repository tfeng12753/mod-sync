// Scenario presets for the demo control panel.
export const SAMPLES = {
  messy: {
    label: 'Happy path: messy order',
    channel: 'DoorDash',
    name: 'Maria',
    itemId: 'classic_burger',
    rawNote: 'No onion pls, sauce on the side, sub fries→side salad, extra pickles',
    customerPrefers: ['garlic_mayo'],
  },
  eggAllergy: {
    label: 'Sad path: egg allergy',
    channel: 'Wolt',
    name: 'Sam',
    itemId: 'classic_burger',
    rawNote: 'allergic to egg, no tomato',
    customerPrefers: ['garlic_mayo'],
  },
  ambiguous: {
    label: 'Sad path: ambiguous note',
    channel: 'Walk-in',
    name: 'Lee',
    itemId: 'chicken_sandwich',
    rawNote: 'make it like last time, no pickles',
  },
  sesame: {
    label: 'Sad path: sesame allergy',
    channel: 'DoorDash',
    name: 'Priya',
    itemId: 'veggie_burger',
    rawNote: 'sesame allergy, no avocado',
  },
}
