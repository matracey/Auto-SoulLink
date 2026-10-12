/* eslint-disable max-len */

// Prototype coverage for pureRGB's Gen 1 patch (calc/calc/src/data/purergb.ts, useDex()). See
// docs/calc_multigen/PURERGB_MECHANICS.md for what this is and isn't modelling yet - this only
// exercises the plumbing (new types resolve, a reskinned move resolves, restoring vanilla works),
// not the alternate-STAB or dynamic-Ghost-category rules that still need a mechanics/gen12.ts
// change gated on a pureRGB flag.
//
// Stat formula (shared with the existing Gen 1/2 tests, see slink_multigen.test.ts): default
// (max) DVs/stat exp, pret/pokered home/move_mon.asm CalcStat -
//   stat = floor(((base + 15) * 2 + 63) * level / 100) + 5

import {useDex} from '../data/purergb';
import {MOVES} from '../data/moves';
import {inGen} from './helper';

afterEach(() => {
  useDex('vanilla'); // never leak pureRGB state into a later test file/run
});

describe('pureRGB Gen 1 patch (prototype)', () => {
  describe('Bonemerang fixed hit count', () => {
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('include both 60-power hits while retaining the custom type', () => {
        useDex('purergb');
        const move = Move('Bonemerang');
        expect(move.hits).toBe(2);
        expect(move.bp).toBe(60);
        expect(move.type).toBe('Bonemerang');
        const result = calculate(Pokemon('Mew', {level: 50}), Pokemon('Mew', {level: 50}), move);
        expect(result.range()).toEqual([46, 56]);
        useDex('vanilla');
        expect(Move('Bonemerang').hits).toBe(2);
        expect(Move('Bonemerang').bp).toBe(50);
      });
    });
  });

  describe('empty move slots', () => {
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('retain the sentinel across dex swaps without changing other generations', () => {
        const gen2Empty = MOVES[2]['(No Move)'];
        for (const dex of ['purergb', 'vanilla', 'purergb'] as const) {
          useDex(dex);
          expect(MOVES[1]['(No Move)']).toEqual({
            bp: 0, type: 'Normal', category: 'Status',
          });
          const move = Move('(No Move)');
          expect(move.bp).toBe(0);
          expect(move.category).toBe('Status');
          expect(calculate(Pokemon('Mew'), Pokemon('Mew'), move).range()).toEqual([0, 0]);
          expect(MOVES[2]['(No Move)']).toBe(gen2Empty);
        }
      });
    });
  });

  describe('new-type effectiveness: Fighting vs Crystal/Ground (Hardened Onix)', () => {
    // data/types/type_matchups.asm: `db FIGHTING, CRYSTAL, SUPER_EFFECTIVE` (2x); Fighting vs
    // Ground has no entry, so it's neutral (1x) - total type multiplier 2x.
    //
    // Machamp (pure Fighting, base atk 130) at level 50:
    //   atk = floor(((130 + 15) * 2 + 63) * 50 / 100) + 5 = floor(353 * 0.5) + 5 = 176 + 5 = 181
    // Hardened Onix (Crystal/Ground, base def 180) at level 50:
    //   def = floor(((180 + 15) * 2 + 63) * 50 / 100) + 5 = floor(453 * 0.5) + 5 = 226 + 5 = 231
    // Karate Chop: 50 BP Fighting, Physical (data/games/gen1_purergb/moves.json id 2).
    //   floor(2*50/5 + 2) = 22
    //   floor(22 * 181 * 50 / 231) = floor(199100 / 231) = 861
    //   floor(861 / 50) = 17, +2 = 19 (baseDamage)
    //   STAB (Machamp is Fighting): floor(19 * 1.5) = 28
    //   type effectiveness (gen 1 applies each defending type separately): floor(28 * 2) = 56,
    //   then floor(56 * 1) = 56 (Ground, the second type, is neutral)
    //   range = [floor(56*217/255), 56] = [floor(47.65), 56] = [47, 56]
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Karate Chop vs Hardened Onix', () => {
        useDex('purergb');

        const onix = Pokemon('Hardened Onix', {level: 50});
        expect(onix.types).toEqual(['Crystal', 'Ground']);

        const machamp = Pokemon('Machamp', {level: 50});
        const result = calculate(machamp, onix, Move('Karate Chop'));
        expect(result.range()).toEqual([47, 56]);
      });
    });
  });

  describe('custom move: Dust Claw (Fury Swipes reskin, data/games/gen1_purergb/moves.json id 154)', () => {
    // Dust Claw: 40 BP Ground, Physical. Sandshrew (Ground/Normal, base atk 75) vs Machamp
    // (pure Fighting, base def 80), both level 50 - Ground vs Fighting has no type_matchups.asm
    // entry, so it's neutral.
    //   atk = floor(((75 + 15) * 2 + 63) * 50 / 100) + 5 = floor(243 * 0.5) + 5 = 121 + 5 = 126
    //   def = floor(((80 + 15) * 2 + 63) * 50 / 100) + 5 = floor(253 * 0.5) + 5 = 126 + 5 = 131
    //   floor(2*50/5 + 2) = 22
    //   floor(22 * 126 * 40 / 131) = floor(110880 / 131) = 846
    //   floor(846 / 50) = 16, +2 = 18 (baseDamage)
    //   STAB (Sandshrew is Ground): floor(18 * 1.5) = 27
    //   no type effectiveness multiplier (neutral both types)
    //   range = [floor(27*217/255), 27] = [floor(22.98), 27] = [22, 27]
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Sandshrew Dust Claw vs Machamp', () => {
        useDex('purergb');

        const move = Move('Dust Claw');
        expect(move.type).toBe('Ground');
        expect(move.category).toBe('Physical');

        const sandshrew = Pokemon('Sandshrew', {level: 50});
        const machamp = Pokemon('Machamp', {level: 50});
        const result = calculate(sandshrew, machamp, move);
        expect(result.range()).toEqual([22, 27]);
      });
    });
  });

  describe('alternate STAB: Tri-type move on a Normal-type attacker (Porygon Tri Attack)', () => {
    // engine/battle/moved_battle_code.asm ShouldMoveGetStabBoost (PURERGB_MECHANICS.md §1.4):
    // Tri-type moves get STAB on Normal-type attackers even though Tri != Normal.
    // Tri Attack: 90 BP Tri, Special (data/games/gen1_purergb/moves.json), so this uses spa/spd.
    // Porygon (pure Normal, base spa 120) at level 50:
    //   spa = floor(((120 + 15) * 2 + 63) * 50 / 100) + 5 = floor(333 * 0.5) + 5 = 166 + 5 = 171
    // Chansey (pure Normal, base spd 105 - Gen 1 has one Special stat) at level 50:
    //   spd = floor(((105 + 15) * 2 + 63) * 50 / 100) + 5 = floor(303 * 0.5) + 5 = 151 + 5 = 156
    //   floor(2*50/5 + 2) = 22
    //   floor(22 * 171 * 90 / 156) = floor(338580 / 156) = 2170
    //   floor(2170 / 50) = 43, +2 = 45 (baseDamage)
    //   alt STAB (Porygon is Normal, Tri Attack is Tri-type -> alt-STAB applies): floor(45*1.5)=67
    //   Tri Attack's real type is Tri, not Normal - Tri vs Normal (defender) is neutral (1x) per
    //   the generated chart, so no further multiplier.
    //   range = [floor(67*217/255), 67] = [floor(57.02), 67] = [57, 67]
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Porygon Tri Attack vs Chansey', () => {
        useDex('purergb');

        const move = Move('Tri Attack');
        expect(move.type).toBe('Tri');
        expect(move.category).toBe('Special');

        const porygon = Pokemon('Porygon', {level: 50});
        expect(porygon.types).toEqual(['Normal']);
        const chansey = Pokemon('Chansey', {level: 50});

        const result = calculate(porygon, chansey, move);
        expect(result.range()).toEqual([57, 67]);
      });

      test('no alt STAB outside pureRGB (vanilla Tri Attack is plain Normal-type)', () => {
        useDex('vanilla');
        expect(Move('Tri Attack').type).toBe('Normal');
      });
    });
  });

  describe("Ghost's dynamic category: Physical/Special per the attacker's own base stats", () => {
    // constants/type_constants.asm: "physical if they're the same or attack is higher" -
    // DynamicTypeCheckPlayer/DynamicTypeCheckEnemy (PURERGB_MECHANICS.md §1.3). Lick is Ghost-type
    // and moves.json's static `split` is "Physical" (the common case), but Gengar's base Special
    // (130) exceeds its base Attack (65), so Lick resolves as Special for Gengar specifically.
    //
    // Gengar (Ghost/Poison, base spa 130) at level 50:
    //   spa = floor(((130 + 15) * 2 + 63) * 50 / 100) + 5 = floor(353 * 0.5) + 5 = 176 + 5 = 181
    // Machamp (pure Fighting, base spd 65) at level 50:
    //   spd = floor(((65 + 15) * 2 + 63) * 50 / 100) + 5 = floor(223 * 0.5) + 5 = 111 + 5 = 116
    // Lick: 35 BP Ghost.
    //   floor(2*50/5 + 2) = 22
    //   floor(22 * 181 * 35 / 116) = floor(139370 / 116) = 1201
    //   floor(1201 / 50) = 24, +2 = 26 (baseDamage)
    //   STAB (Gengar is Ghost): floor(26 * 1.5) = 39
    //   type effectiveness: Ghost vs Fighting is neutral (1x) in the generated chart.
    //   range = [floor(39*217/255), 39] = [floor(33.15), 39] = [33, 39]
    //
    // Using the STATIC "Physical" split instead (i.e. without the dynamic-category override) would
    // use Machamp's def (131, per the Karate Chop test above) and Gengar's atk (base 65 ->
    // floor(((65+15)*2+63)*50/100)+5 = 116) for a very different (much lower) result - this test
    // only passes if the override actually fires.
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Gengar Lick vs Machamp resolves Special (base Special > base Attack)', () => {
        useDex('purergb');

        const move = Move('Lick');
        expect(move.type).toBe('Ghost');
        expect(move.category).toBe('Physical'); // moves.json's static (common-case) split

        const gengar = Pokemon('Gengar', {level: 50});
        expect(gengar.species.baseStats.spa).toBeGreaterThan(gengar.species.baseStats.atk);
        const machamp = Pokemon('Machamp', {level: 50});

        const result = calculate(gengar, machamp, move);
        expect(result.range()).toEqual([33, 39]);
      });
    });
  });

  describe('Siphon Snag drains HP like vanilla Absorb/Mega Drain', () => {
    // engine/battle/move_effects/siphon_snag.asm (_SiphonSnagEffect) - PURERGB_MECHANICS.md §2.
    inGen(1, ({Move}) => {
      test('Siphon Snag carries drain: [1, 2]', () => {
        useDex('purergb');
        expect(Move('Siphon Snag').drain).toEqual([1, 2]);
      });
    });
  });

  describe('Night Shade and Sonic Boom are no longer fixed-damage in pureRGB', () => {
    // data/moves/moves.asm (pureRGB source): NIGHT_SHADE carries effect NO_ADDITIONAL_EFFECT
    // (power 65) and SONICBOOM carries FLINCH_SIDE_EFFECT1 (power 50) - neither carries
    // SPECIAL_DAMAGE_EFFECT any more (only SEISMIC_TOSS still does). Without the pureRGB-Gen1
    // gate in mechanics/util.ts's handleFixedDamageMoves, these would incorrectly compute as
    // attacker.level / 20 (vanilla's fixed-damage formula) purely because their (renamed) names
    // match vanilla's own fixed-damage move list.
    //
    // Sonic Boom is also Ghost-type, so - same as Lick above - Gengar's dynamic category makes it
    // Special here too (moves.json's static split is "Physical", the common case; it's the
    // per-attacker override that fires, same rule, not a second bug).
    //
    // Gengar (Ghost/Poison) using its own Sonic Boom (50 BP Ghost) against Machamp (pure
    // Fighting) at level 50, both using spa/spd (dynamic Special):
    //   spa = 181, spd = 116 (Gengar/Machamp, from the Ghost-dynamic test above)
    //   floor(2*50/5+2) = 22
    //   floor(22*181*50/116) = floor(199100/116) = 1716
    //   floor(1716/50) = 34, +2 = 36 (baseDamage)
    //   STAB (Gengar is Ghost, Sonic Boom is Ghost): floor(36*1.5) = 54
    //   Ghost vs Fighting neutral (1x).
    //   range = [floor(54*217/255), 54] = [floor(45.95), 54] = [45, 54]
    // If this were still treated as vanilla's fixed-20 Sonic Boom, every roll in the range would
    // be exactly 20 instead.
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Sonic Boom deals normal power-based damage, not a fixed 20', () => {
        useDex('purergb');

        const move = Move('Sonic Boom');
        expect(move.type).toBe('Ghost');
        expect(move.category).toBe('Physical'); // moves.json's static (common-case) split

        const gengar = Pokemon('Gengar', {level: 50});
        const machamp = Pokemon('Machamp', {level: 50});
        const result = calculate(gengar, machamp, move);
        expect(result.range()).toEqual([45, 54]);
      });

      test('Seismic Toss is still fixed-damage (unchanged from vanilla)', () => {
        useDex('purergb');
        const move = Move('Seismic Toss');
        const attacker = Pokemon('Machamp', {level: 50});
        const defender = Pokemon('Chansey', {level: 50});
        const result = calculate(attacker, defender, move);
        expect(result.range()).toEqual([50, 50]);
      });
    });
  });

  describe("useDex('vanilla') restores vanilla Gen 1", () => {
    // Same Mew/Pound case as slink_multigen.test.ts's "Gen 1 critical hit" - if this still passes
    // after a purergb round trip, the Species/Moves.prototype patch and the Gen 1 type chart both
    // came back cleanly.
    inGen(1, ({calculate, Pokemon, Move}) => {
      test('Mew Pound non-crit after a purergb round trip', () => {
        useDex('purergb');
        expect(Pokemon('Sandshrew').types).toEqual(['Ground', 'Normal']);

        useDex('vanilla');
        const mew = Pokemon('Mew', {level: 50});
        const target = Pokemon('Mew', {level: 50});
        const result = calculate(mew, target, Move('Pound'));
        expect(result.range()).toEqual([16, 19]);

        // pureRGB-only species/moves/types are gone again
        expect(Pokemon('Sandshrew').types).toEqual(['Ground']);
        expect(target.types).toEqual(['Psychic']);
      });
    });
  });
});
