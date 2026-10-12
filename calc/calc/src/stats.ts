import {Natures, Generation, TypeName, StatID, StatsTable} from './data/interface';
import {toID} from './util';

const RBY: Array<StatID | 'spc'> = ['hp', 'atk', 'def', 'spc', 'spe'];
const GSC: StatID[] = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
const ADV: StatID[] = GSC;
const DPP: StatID[] = GSC;
const BW: StatID[] = GSC;
const XY: StatID[] = GSC;
const SM: StatID[] = GSC;
const SS: StatID[] = GSC;
const SV: StatID[] = GSC;

export const STATS: Array<Array<StatID | 'spc'> | StatID[]> =
  [[], RBY, GSC, ADV, DPP, BW, XY, SM, SS, SV];

// pureRGB's 6 extra Gen 1 types (docs/calc_multigen/PURERGB_MECHANICS.md) are excluded here too -
// Hidden Power doesn't exist until Gen 2, and none of pureRGB's types apply there.
type HPTypeName = Exclude<TypeName,
'Normal' | 'Fairy' | 'Stellar' | '???' |
'Crystal' | 'Bonemerang' | 'Tri' | 'Floating' | 'Magma' | 'Typeless'>;

const HP_TYPES = [
  'Fighting', 'Flying', 'Poison', 'Ground', 'Rock', 'Bug', 'Ghost', 'Steel',
  'Fire', 'Water', 'Grass', 'Electric', 'Psychic', 'Ice', 'Dragon', 'Dark',
];

const HP: {[type in HPTypeName]: {ivs: Partial<StatsTable>; dvs: Partial<StatsTable>}} = {
  Bug: {ivs: {atk: 30, def: 30, spd: 30}, dvs: {atk: 13, def: 13}},
  Dark: {ivs: {}, dvs: {}},
  Dragon: {ivs: {atk: 30}, dvs: {def: 14}},
  Electric: {ivs: {spa: 30}, dvs: {atk: 14}},
  Fighting: {ivs: {def: 30, spa: 30, spd: 30, spe: 30}, dvs: {atk: 12, def: 12}},
  Fire: {ivs: {atk: 30, spa: 30, spe: 30}, dvs: {atk: 14, def: 12}},
  Flying: {ivs: {hp: 30, atk: 30, def: 30, spa: 30, spd: 30}, dvs: {atk: 12, def: 13}},
  Ghost: {ivs: {def: 30, spd: 30}, dvs: {atk: 13, def: 14}},
  Grass: {ivs: {atk: 30, spa: 30}, dvs: {atk: 14, def: 14}},
  Ground: {ivs: {spa: 30, spd: 30}, dvs: {atk: 12}},
  Ice: {ivs: {atk: 30, def: 30}, dvs: {def: 13}},
  Poison: {ivs: {def: 30, spa: 30, spd: 30}, dvs: {atk: 12, def: 14}},
  Psychic: {ivs: {atk: 30, spe: 30}, dvs: {def: 12}},
  Rock: {ivs: {def: 30, spd: 30, spe: 30}, dvs: {atk: 13, def: 12}},
  Steel: {ivs: {spd: 30}, dvs: {atk: 13}},
  Water: {ivs: {atk: 30, def: 30, spa: 30}, dvs: {atk: 14, def: 13}},
};

export const Stats = new (class {
  displayStat(stat: StatID | 'spc') {
    switch (stat) {
    case 'hp':
      return 'HP';
    case 'atk':
      return 'Atk';
    case 'def':
      return 'Def';
    case 'spa':
      return 'SpA';
    case 'spd':
      return 'SpD';
    case 'spe':
      return 'Spe';
    case 'spc':
      return 'Spc';
    default:
      throw new Error(`unknown stat ${stat}`);
    }
  }

  shortForm(stat: StatID | 'spc') {
    switch (stat) {
    case 'hp':
      return 'hp';
    case 'atk':
      return 'at';
    case 'def':
      return 'df';
    case 'spa':
      return 'sa';
    case 'spd':
      return 'sd';
    case 'spe':
      return 'sp';
    case 'spc':
      return 'sl';
    }
  }

  getHPDV(ivs: {atk: number; def: number; spe: number; spc: number}) {
    return (
      (this.IVToDV(ivs.atk) % 2) * 8 +
      (this.IVToDV(ivs.def) % 2) * 4 +
      (this.IVToDV(ivs.spe) % 2) * 2 +
      (this.IVToDV(ivs.spc) % 2)
    );
  }

  IVToDV(iv: number) {
    return Math.floor(iv / 2);
  }

  DVToIV(dv: number) {
    return dv * 2;
  }

  DVsToIVs(dvs: Readonly<Partial<StatsTable>>) {
    const ivs: Partial<StatsTable> = {};
    let dv: StatID;
    for (dv in dvs) {
      ivs[dv] = Stats.DVToIV(dvs[dv]!);
    }
    return ivs;
  }

  calcStat(
    gen: Generation,
    stat: StatID,
    base: number,
    iv: number,
    ev: number,
    level: number,
    nature?: string
  ) {
    if (gen.num < 1 || gen.num > 9) throw new Error(`Invalid generation ${gen.num}`);
    // SLink: in Gen 1/2 `ev` is raw stat experience (0-65535), the same meaning Pokemon uses, so
    // the UI's stat totals (shared_controls calcStat) match the damage calc.
    if (gen.num < 3) return this.calcStatRBY(stat, base, iv, level, ev);
    return this.calcStatADV(gen.natures, stat, base, iv, ev, level, nature);
  }

  calcStatADV(
    natures: Natures,
    stat: StatID,
    base: number,
    iv: number,
    ev: number,
    level: number,
    nature?: string
  ) {
    if (stat === 'hp') {
      return base === 1
        ? base
        : Math.floor(((base * 2 + iv + Math.floor(ev / 4)) * level) / 100) + level + 10;
    } else {
      let mods: [StatID?, StatID?] = [undefined, undefined];
      if (nature) {
        const nat = natures.get(toID(nature));
        mods = [nat?.plus, nat?.minus];
      }
      const n =
        mods[0] === stat && mods[1] === stat
          ? 1
          : mods[0] === stat
            ? 1.1
            : mods[1] === stat
              ? 0.9
              : 1;

      return Math.floor((Math.floor(((base * 2 + iv + Math.floor(ev / 4)) * level) / 100) + 5) * n);
    }
  }

  // statExp defaults to 65535 (max), which reproduces the old hard-coded "+ 63" term below -
  // every trainer/wild mon before this fix implicitly had max stat exp, so this keeps existing
  // output unchanged unless a caller passes a real value (0-65535).
  calcStatRBY(stat: StatID, base: number, iv: number, level: number, statExp = 65535) {
    return this.calcStatRBYFromDV(stat, base, this.IVToDV(iv), level, statExp);
  }

  // Formula + the 255 sqrt cap: pret/pokered home/move_mon.asm CalcStat (loop at lines 54-93,
  // `srl b; srl b` divide-by-4 at line 163-164) and pret/pokecrystal engine/pokemon/move_mon.asm
  // CalcMonStatC (line ~1424) + engine/math/get_square_root.asm GetSquareRoot (NUM_SQUARE_ROOTS
  // = 255, line 1) - the game increments a root candidate up to 255 and stops even if 255^2 is
  // still short of statExp, so true ceil(sqrt(65535)) = 256 is capped to 255 (floor(255/4) = 63,
  // matching the old hard-coded constant).
  calcStatRBYFromDV(stat: StatID, base: number, dv: number, level: number, statExp = 65535) {
    const bonus = Math.floor(Math.min(255, Math.ceil(Math.sqrt(statExp))) / 4);
    if (stat === 'hp') {
      return Math.floor((((base + dv) * 2 + bonus) * level) / 100) + level + 10;
    } else {
      return Math.floor((((base + dv) * 2 + bonus) * level) / 100) + 5;
    }
  }

  getHiddenPowerIVs(gen: Generation, hpType: HPTypeName) {
    const hp = HP[hpType];
    if (!hp) return undefined;
    return gen.num === 2 ? Stats.DVsToIVs(hp.dvs) : hp.ivs;
  }

  getHiddenPower(gen: Generation, ivs: StatsTable) {
    const tr = (num: number, bits = 0) => {
      if (bits) return (num >>> 0) % (2 ** bits);
      return num >>> 0;
    };
    const stats = {hp: 31, atk: 31, def: 31, spe: 31, spa: 31, spd: 31};
    if (gen.num <= 2) {
      // Gen 2 specific Hidden Power check. IVs are still treated 0-31 so we get them 0-15
      const atkDV = tr(ivs.atk / 2);
      const defDV = tr(ivs.def / 2);
      const speDV = tr(ivs.spe / 2);
      const spcDV = tr(ivs.spa / 2);
      return {
        type: HP_TYPES[4 * (atkDV % 4) + (defDV % 4)] as TypeName,
        power: tr(
          (5 * ((spcDV >> 3) +
            (2 * (speDV >> 3)) +
            (4 * (defDV >> 3)) +
            (8 * (atkDV >> 3))) +
            (spcDV % 4)) / 2 + 31
        ),
      };
    } else {
      // Hidden Power check for Gen 3 onwards
      let hpTypeX = 0;
      let hpPowerX = 0;
      let i = 1;
      for (const s in stats) {
        hpTypeX += i * (ivs[s as StatID] % 2);
        hpPowerX += i * (tr(ivs[s as StatID] / 2) % 2);
        i *= 2;
      }
      return {
        type: HP_TYPES[tr(hpTypeX * 15 / 63)] as TypeName,
        // After Gen 6, Hidden Power is always 60 base power
        power: (gen.num && gen.num < 6) ? tr(hpPowerX * 40 / 63) + 30 : 60,
      };
    }
  }
})();
