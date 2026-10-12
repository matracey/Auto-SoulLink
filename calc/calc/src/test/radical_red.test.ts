import {Generations, Move, Pokemon} from '../index';
import {toID} from '../util';

describe('Radical Red data isolation', () => {
  test('custom abilities stay in the RR slot', () => {
    const rr = Generations.get(9);
    for (const name of ['Feline Prowess', 'Sage Power', 'Bull Rush', 'Primal Armor']) {
      expect(rr.abilities.get(toID(name))?.name).toBe(name);
      expect(Generations.get(8).abilities.get(toID(name))).toBeUndefined();
    }
  });

  test.each([
    ['Alcremite', 'Alcremie'], ['Appletunite', 'Appletun'], ['Butterfrite', 'Butterfree'],
    ['Centiskite', 'Centiskorch'], ['Coalossite', 'Coalossal'], ['Copperajite', 'Copperajah'],
    ['Drednawite', 'Drednaw'], ['Duraludonite', 'Duraludon'], ['Flapplite', 'Flapple'],
    ['Garbodorite', 'Garbodor'], ['Kinglerite', 'Kingler'], ['Laprasite', 'Lapras'],
    ['Machampite', 'Machamp'], ['Orbeetlite', 'Orbeetle'], ['Sandacondite', 'Sandaconda'],
    ['Snorlaxite', 'Snorlax'], ['Toxtricitite', 'Toxtricity'],
    ['Toxtricitite Low Key', 'Toxtricity-Low-Key'],
  ])('%s evolves %s only in RR', (item, species) => {
    expect(Generations.get(9).items.get(toID(item))?.megaEvolves).toBe(species);
    for (const gen of [6, 7, 8] as const) {
      expect(Generations.get(gen).items.get(toID(item))).toBeUndefined();
    }
  });

  test('retail Stick and RR Leek Stick remain distinct', () => {
    expect(Generations.get(2).items.get(toID('Stick'))?.name).toBe('Stick');
    expect(Generations.get(8).items.get(toID('Leek Stick'))).toBeUndefined();
    expect(Generations.get(9).items.get(toID('Stick'))).toBeUndefined();
    expect(Generations.get(9).items.get(toID('Leek Stick'))?.name).toBe('Leek Stick');
    expect(Generations.get(9).items.get(toID('Burnt Seed'))?.name).toBe('Burnt Seed');
  });

  test('move rebalances do not leak into retail generations', () => {
    expect(new Move(3, 'Muddy Water').bp).toBe(95);
    expect(new Move(6, 'Muddy Water').bp).toBe(90);
    expect(new Move(8, 'Muddy Water').bp).toBe(90);
    expect(new Move(9, 'Muddy Water').bp).toBe(95);
    expect(new Move(8, 'Leech Life', {useZ: true}).bp).toBe(160);
    expect(new Move(9, 'Leech Life', {useZ: true}).bp).toBe(100);
  });

  test('Jellicent and Meltan retain their RR rebalances', () => {
    expect(new Pokemon(8, 'Jellicent').species.baseStats).toMatchObject({def: 70, spa: 85});
    expect(new Pokemon(9, 'Jellicent').species.baseStats).toMatchObject({def: 80, spa: 75});
    expect(new Pokemon(8, 'Meltan').species.nfe).toBeUndefined();
    expect(new Pokemon(9, 'Meltan').species.nfe).toBe(true);
  });

  test('mechanics fixtures do not replace RR species stats', () => {
    expect(new Pokemon(9, 'Castform').species.baseStats.spa).toBe(85);
    expect(new Pokemon(9, 'Mismagius').species.baseStats.hp).toBe(70);
    expect(new Pokemon(9, 'Milotic').species.baseStats.def).toBe(84);
  });

  test('Gigantamax forms remain available in gen 8, not RR', () => {
    expect(new Pokemon(8, 'Venusaur-Gmax').weightkg).toBe(100);
    expect(Generations.get(9).species.get(toID('Venusaur-Gmax'))).toBeUndefined();
    for (const species of Generations.get(9).species) {
      expect(species.otherFormes?.some(name => name.endsWith('-Gmax')) ?? false).toBe(false);
    }
  });

  test('RR keeps cosplay forms and partner moves independently of gen 8', () => {
    expect(Generations.get(8).species.get(toID('Pikachu-Cosplay'))).toBeUndefined();
    expect(Generations.get(9).species.get(toID('Pikachu-Cosplay'))?.name).toBe('Pikachu-Cosplay');
    expect(Generations.get(8).moves.get(toID('Bouncy Bubble'))).toBeUndefined();
    expect(new Move(9, 'Bouncy Bubble').bp).toBe(90);
  });
});
