"""
server/pokemon_data.py — Shared Pokémon data tables and helpers.

Extracted from server.py so that both server.py and state.py can
import species names, gender ratios, type data, and evolution families
without circular dependencies.

NOTE: Species IDs use Radical Red 4.1 (CFRU/DPE) internal ordering, NOT National Dex.
Gen 1–2 IDs (1–251) match NatDex. Gen 3+ uses CFRU IDs which diverge.
Gen 9 and RR-exclusive Sevii forms occupy custom slots.
Regenerate with: python tools/gen_rr_species.py + tools/gen_rr_natdex.py
RR families: python tools/gen_rr_evolutions.py --rom <clean RR ROM>
"""

import json as _family_json
from pathlib import Path as _FamilyPath

# Required RR data, generated from the admitted ROM. Never fall back to the
# generic name chains when a regional evolution is missing from those chains.
_rr_family_path = _FamilyPath(__file__).resolve().parents[1] / "data/games/gen3_rr/evolution_families.json"
_RR_EVO_FAMILY = {int(k): v for k, v in _family_json.loads(
    _rr_family_path.read_text(encoding="utf-8"))["families"].items()}
del _family_json, _FamilyPath, _rr_family_path

# ── RR 4.1 internal species IDs → display names (1329 species) ──────────────
# Keyed by RR internal ID (NOT national dex number).
# Generated from funnotbun/funnotbun.github.io species.h
SPECIES_NAMES: dict[int, str] = {
    1:'Bulbasaur',2:'Ivysaur',3:'Venusaur',4:'Charmander',5:'Charmeleon',
    6:'Charizard',7:'Squirtle',8:'Wartortle',9:'Blastoise',10:'Caterpie',
    11:'Metapod',12:'Butterfree',13:'Weedle',14:'Kakuna',15:'Beedrill',
    16:'Pidgey',17:'Pidgeotto',18:'Pidgeot',19:'Rattata',20:'Raticate',
    21:'Spearow',22:'Fearow',23:'Ekans',24:'Arbok',25:'Pikachu',
    26:'Raichu',27:'Sandshrew',28:'Sandslash',29:'Nidoran♀',30:'Nidorina',
    31:'Nidoqueen',32:'Nidoran♂',33:'Nidorino',34:'Nidoking',35:'Clefairy',
    36:'Clefable',37:'Vulpix',38:'Ninetales',39:'Jigglypuff',40:'Wigglytuff',
    41:'Zubat',42:'Golbat',43:'Oddish',44:'Gloom',45:'Vileplume',
    46:'Paras',47:'Parasect',48:'Venonat',49:'Venomoth',50:'Diglett',
    51:'Dugtrio',52:'Meowth',53:'Persian',54:'Psyduck',55:'Golduck',
    56:'Mankey',57:'Primeape',58:'Growlithe',59:'Arcanine',60:'Poliwag',
    61:'Poliwhirl',62:'Poliwrath',63:'Abra',64:'Kadabra',65:'Alakazam',
    66:'Machop',67:'Machoke',68:'Machamp',69:'Bellsprout',70:'Weepinbell',
    71:'Victreebel',72:'Tentacool',73:'Tentacruel',74:'Geodude',75:'Graveler',
    76:'Golem',77:'Ponyta',78:'Rapidash',79:'Slowpoke',80:'Slowbro',
    81:'Magnemite',82:'Magneton',83:"Farfetch'd",84:'Doduo',85:'Dodrio',
    86:'Seel',87:'Dewgong',88:'Grimer',89:'Muk',90:'Shellder',
    91:'Cloyster',92:'Gastly',93:'Haunter',94:'Gengar',95:'Onix',
    96:'Drowzee',97:'Hypno',98:'Krabby',99:'Kingler',100:'Voltorb',
    101:'Electrode',102:'Exeggcute',103:'Exeggutor',104:'Cubone',105:'Marowak',
    106:'Hitmonlee',107:'Hitmonchan',108:'Lickitung',109:'Koffing',110:'Weezing',
    111:'Rhyhorn',112:'Rhydon',113:'Chansey',114:'Tangela',115:'Kangaskhan',
    116:'Horsea',117:'Seadra',118:'Goldeen',119:'Seaking',120:'Staryu',
    121:'Starmie',122:'Mr. Mime',123:'Scyther',124:'Jynx',125:'Electabuzz',
    126:'Magmar',127:'Pinsir',128:'Tauros',129:'Magikarp',130:'Gyarados',
    131:'Lapras',132:'Ditto',133:'Eevee',134:'Vaporeon',135:'Jolteon',
    136:'Flareon',137:'Porygon',138:'Omanyte',139:'Omastar',140:'Kabuto',
    141:'Kabutops',142:'Aerodactyl',143:'Snorlax',144:'Articuno',145:'Zapdos',
    146:'Moltres',147:'Dratini',148:'Dragonair',149:'Dragonite',150:'Mewtwo',
    151:'Mew',152:'Chikorita',153:'Bayleef',154:'Meganium',155:'Cyndaquil',
    156:'Quilava',157:'Typhlosion',158:'Totodile',159:'Croconaw',160:'Feraligatr',
    161:'Sentret',162:'Furret',163:'Hoothoot',164:'Noctowl',165:'Ledyba',
    166:'Ledian',167:'Spinarak',168:'Ariados',169:'Crobat',170:'Chinchou',
    171:'Lanturn',172:'Pichu',173:'Cleffa',174:'Igglybuff',175:'Togepi',
    176:'Togetic',177:'Natu',178:'Xatu',179:'Mareep',180:'Flaaffy',
    181:'Ampharos',182:'Bellossom',183:'Marill',184:'Azumarill',185:'Sudowoodo',
    186:'Politoed',187:'Hoppip',188:'Skiploom',189:'Jumpluff',190:'Aipom',
    191:'Sunkern',192:'Sunflora',193:'Yanma',194:'Wooper',195:'Quagsire',
    196:'Espeon',197:'Umbreon',198:'Murkrow',199:'Slowking',200:'Misdreavus',
    201:'Unown',202:'Wobbuffet',203:'Girafarig',204:'Pineco',205:'Forretress',
    206:'Dunsparce',207:'Gligar',208:'Steelix',209:'Snubbull',210:'Granbull',
    211:'Qwilfish',212:'Scizor',213:'Shuckle',214:'Heracross',215:'Sneasel',
    216:'Teddiursa',217:'Ursaring',218:'Slugma',219:'Magcargo',220:'Swinub',
    221:'Piloswine',222:'Corsola',223:'Remoraid',224:'Octillery',225:'Delibird',
    226:'Mantine',227:'Skarmory',228:'Houndour',229:'Houndoom',230:'Kingdra',
    231:'Phanpy',232:'Donphan',233:'Porygon2',234:'Stantler',235:'Smeargle',
    236:'Tyrogue',237:'Hitmontop',238:'Smoochum',239:'Elekid',240:'Magby',
    241:'Miltank',242:'Blissey',243:'Raikou',244:'Entei',245:'Suicune',
    246:'Larvitar',247:'Pupitar',248:'Tyranitar',249:'Lugia',250:'Ho-Oh',
    251:'Celebi',277:'Treecko',278:'Grovyle',279:'Sceptile',280:'Torchic',
    281:'Combusken',282:'Blaziken',283:'Mudkip',284:'Marshtomp',285:'Swampert',
    286:'Poochyena',287:'Mightyena',288:'Zigzagoon',289:'Linoone',290:'Wurmple',
    291:'Silcoon',292:'Beautifly',293:'Cascoon',294:'Dustox',295:'Lotad',
    296:'Lombre',297:'Ludicolo',298:'Seedot',299:'Nuzleaf',300:'Shiftry',
    301:'Nincada',302:'Ninjask',303:'Shedinja',304:'Taillow',305:'Swellow',
    306:'Shroomish',307:'Breloom',308:'Spinda',309:'Wingull',310:'Pelipper',
    311:'Surskit',312:'Masquerain',313:'Wailmer',314:'Wailord',315:'Skitty',
    316:'Delcatty',317:'Kecleon',318:'Baltoy',319:'Claydol',320:'Nosepass',
    321:'Torkoal',322:'Sableye',323:'Barboach',324:'Whiscash',325:'Luvdisc',
    326:'Corphish',327:'Crawdaunt',328:'Feebas',329:'Milotic',330:'Carvanha',
    331:'Sharpedo',332:'Trapinch',333:'Vibrava',334:'Flygon',335:'Makuhita',
    336:'Hariyama',337:'Electrike',338:'Manectric',339:'Numel',340:'Camerupt',
    341:'Spheal',342:'Sealeo',343:'Walrein',344:'Cacnea',345:'Cacturne',
    346:'Snorunt',347:'Glalie',348:'Lunatone',349:'Solrock',350:'Azurill',
    351:'Spoink',352:'Grumpig',353:'Plusle',354:'Minun',355:'Mawile',
    356:'Meditite',357:'Medicham',358:'Swablu',359:'Altaria',360:'Wynaut',
    361:'Duskull',362:'Dusclops',363:'Roselia',364:'Slakoth',365:'Vigoroth',
    366:'Slaking',367:'Gulpin',368:'Swalot',369:'Tropius',370:'Whismur',
    371:'Loudred',372:'Exploud',373:'Clamperl',374:'Huntail',375:'Gorebyss',
    376:'Absol',377:'Shuppet',378:'Banette',379:'Seviper',380:'Zangoose',
    381:'Relicanth',382:'Aron',383:'Lairon',384:'Aggron',385:'Castform',
    386:'Volbeat',387:'Illumise',388:'Lileep',389:'Cradily',390:'Anorith',
    391:'Armaldo',392:'Ralts',393:'Kirlia',394:'Gardevoir',395:'Bagon',
    396:'Shelgon',397:'Salamence',398:'Beldum',399:'Metang',400:'Metagross',
    401:'Regirock',402:'Regice',403:'Registeel',404:'Kyogre',405:'Groudon',
    406:'Rayquaza',407:'Latias',408:'Latios',409:'Jirachi',410:'Deoxys',
    411:'Chimecho',412:'Egg',413:'Unown B',414:'Unown C',415:'Unown D',416:'Unown E',
    417:'Unown (Female)',418:'Unown-Galar',419:'Unown-Hisui',420:'Unown I',421:'Unown J',
    422:'Unown K',423:'Unown (L)',424:'Unown (M)',425:'Unown N',426:'Unown (Origin)',
    427:'Unown-Paldea',428:'Unown Q',429:'Unown R',430:'Unown-Sevii',431:'Unown T',
    432:'Unown U',433:'Unown V',434:'Unown W',435:'Unown X',436:'Unown Y',
    437:'Unown Z',438:'Unown Exclamation',439:'Unown Question',440:'Turtwig',441:'Grotle',
    442:'Torterra',443:'Chimchar',444:'Monferno',445:'Infernape',446:'Piplup',
    447:'Prinplup',448:'Empoleon',449:'Starly',450:'Staravia',451:'Staraptor',
    452:'Bidoof',453:'Bibarel',454:'Kricketot',455:'Kricketune',456:'Shinx',
    457:'Luxio',458:'Luxray',459:'Budew',460:'Roserade',461:'Cranidos',
    462:'Rampardos',463:'Shieldon',464:'Bastiodon',465:'Burmy',466:'Wormadam',
    467:'Mothim',468:'Combee',469:'Vespiquen',470:'Pachirisu',471:'Buizel',
    472:'Floatzel',473:'Cherubi',474:'Cherrim',475:'Shellos',476:'Gastrodon',
    477:'Ambipom',478:'Drifloon',479:'Drifblim',480:'Buneary',481:'Lopunny',
    482:'Mismagius',483:'Honchkrow',484:'Glameow',485:'Purugly',486:'Chingling',
    487:'Stunky',488:'Skuntank',489:'Bronzor',490:'Bronzong',491:'Bonsly',
    492:'Mime Jr.',493:'Happiny',494:'Chatot',495:'Spiritomb',496:'Gible',
    497:'Gabite',498:'Garchomp',499:'Munchlax',500:'Riolu',501:'Lucario',
    502:'Hippopotas',503:'Hippowdon',504:'Skorupi',505:'Drapion',506:'Croagunk',
    507:'Toxicroak',508:'Carnivine',509:'Finneon',510:'Lumineon',511:'Mantyke',
    512:'Snover',513:'Abomasnow',514:'Weavile',515:'Magnezone',516:'Lickilicky',
    517:'Rhyperior',518:'Tangrowth',519:'Electivire',520:'Magmortar',521:'Togekiss',
    522:'Yanmega',523:'Leafeon',524:'Glaceon',525:'Gliscor',526:'Mamoswine',
    527:'Porygon-Z',528:'Gallade',529:'Probopass',530:'Dusknoir',531:'Froslass',
    532:'Rotom',533:'Uxie',534:'Mesprit',535:'Azelf',536:'Dialga',
    537:'Palkia',538:'Heatran',539:'Regigigas',540:'Giratina',541:'Cresselia',
    542:'Phione',543:'Manaphy',544:'Darkrai',545:'Shaymin',546:'Arceus',
    547:'Victini',548:'Snivy',549:'Servine',550:'Serperior',551:'Tepig',
    552:'Pignite',553:'Emboar',554:'Oshawott',555:'Dewott',556:'Samurott',
    557:'Patrat',558:'Watchog',559:'Lillipup',560:'Herdier',561:'Stoutland',
    562:'Purrloin',563:'Liepard',564:'Pansage',565:'Simisage',566:'Pansear',
    567:'Simisear',568:'Panpour',569:'Simipour',570:'Munna',571:'Musharna',
    572:'Pidove',573:'Tranquill',574:'Unfezant',575:'Blitzle',576:'Zebstrika',
    577:'Roggenrola',578:'Boldore',579:'Gigalith',580:'Woobat',581:'Swoobat',
    582:'Drilbur',583:'Excadrill',584:'Audino',585:'Timburr',586:'Gurdurr',
    587:'Conkeldurr',588:'Tympole',589:'Palpitoad',590:'Seismitoad',591:'Throh',
    592:'Sawk',593:'Sewaddle',594:'Swadloon',595:'Leavanny',596:'Venipede',
    597:'Whirlipede',598:'Scolipede',599:'Cottonee',600:'Whimsicott',601:'Petilil',
    602:'Lilligant',603:'Basculin (Red)',604:'Sandile',605:'Krokorok',606:'Krookodile',
    607:'Darumaka',608:'Darmanitan',609:'Maractus',610:'Dwebble',611:'Crustle',
    612:'Scraggy',613:'Scrafty',614:'Sigilyph',615:'Yamask',616:'Cofagrigus',
    617:'Tirtouga',618:'Carracosta',619:'Archen',620:'Archeops',621:'Trubbish',
    622:'Garbodor',623:'Zorua',624:'Zoroark',625:'Minccino',626:'Cinccino',
    627:'Gothita',628:'Gothorita',629:'Gothitelle',630:'Solosis',631:'Duosion',
    632:'Reuniclus',633:'Ducklett',634:'Swanna',635:'Vanillite',636:'Vanillish',
    637:'Vanilluxe',638:'Deerling',639:'Sawsbuck',640:'Emolga',641:'Karrablast',
    642:'Escavalier',643:'Foongus',644:'Amoonguss',645:'Frillish',646:'Jellicent',
    647:'Alomomola',648:'Joltik',649:'Galvantula',650:'Ferroseed',651:'Ferrothorn',
    652:'Klink',653:'Klang',654:'Klinklang',655:'Tynamo',656:'Eelektrik',
    657:'Eelektross',658:'Elgyem',659:'Beheeyem',660:'Litwick',661:'Lampent',
    662:'Chandelure',663:'Axew',664:'Fraxure',665:'Haxorus',666:'Cubchoo',
    667:'Beartic',668:'Cryogonal',669:'Shelmet',670:'Accelgor',671:'Stunfisk',
    672:'Mienfoo',673:'Mienshao',674:'Druddigon',675:'Golett',676:'Golurk',
    677:'Pawniard',678:'Bisharp',679:'Bouffalant',680:'Rufflet',681:'Braviary',
    682:'Vullaby',683:'Mandibuzz',684:'Heatmor',685:'Durant',686:'Deino',
    687:'Zweilous',688:'Hydreigon',689:'Larvesta',690:'Volcarona',691:'Cobalion',
    692:'Terrakion',693:'Virizion',694:'Tornadus',695:'Thundurus',696:'Reshiram',
    697:'Zekrom',698:'Landorus',699:'Kyurem',700:'Keldeo',701:'Meloetta',
    702:'Genesect',703:'Unfezant (Female)',704:'Frillish (Female)',705:'Jellicent (Female)',706:'Klawf',
    707:'Burmy (Sandy)',708:'Burmy (Trash)',709:'Wormadam (Sandy)',710:'Wormadam (Trash)',711:'Shellos (East)',
    712:'Gastrodon (East)',713:'Rotom (Heat)',714:'Rotom (Wash)',715:'Rotom (Frost)',716:'Rotom (Fan)',
    717:'Rotom (Mow)',718:'Giratina (Origin)',719:'Shaymin (Sky)',720:'Arceus (Fight)',721:'Arceus (Flying)',
    722:'Arceus (Poison)',723:'Arceus (Ground)',724:'Arceus (Rock)',725:'Arceus (Bug)',726:'Arceus (Ghost)',
    727:'Arceus (Steel)',728:'Arceus (Fire)',729:'Arceus (Water)',730:'Arceus (Grass)',731:'Arceus (Electric)',
    732:'Arceus (Psychic)',733:'Arceus (Ice)',734:'Arceus (Dragon)',735:'Arceus (Dark)',736:'Basculin (Blue)',
    737:'Darmanitan (Zen)',738:'Deerling Summer',739:'Deerling Autumn',740:'Deerling Winter',741:'Sawsbuck Summer',
    742:'Sawsbuck Autumn',743:'Sawsbuck Winter',744:'Hippopotas (Female)',745:'Hippowdon (Female)',746:'Meloetta (Pirouette)',
    747:'Ting-Lu',748:'Chien-Pao',749:'Wo-Chien',750:'Chi-Yu',751:'Cherrim (Sun)',
    752:'Kyurem (Black)',753:'Kyurem (White)',754:'Tornadus (Therian)',755:'Thundurus (Therian)',756:'Landorus (Therian)',
    757:'Keldeo (Resolute)',758:'Chespin',759:'Quilladin',760:'Chesnaught',761:'Fennekin',
    762:'Braixen',763:'Delphox',764:'Froakie',765:'Frogadier',766:'Greninja',
    767:'Bunnelby',768:'Diggersby',769:'Fletchling',770:'Fletchinder',771:'Talonflame',
    772:'Scatterbug',773:'Spewpa',774:'Vivillon',775:'Litleo',776:'Pyroar',
    777:'Flabebe',778:'Floette',779:'Florges',780:'Skiddo',781:'Gogoat',
    782:'Pancham',783:'Pangoro',784:'Furfrou',785:'Espurr',786:'Meowstic',
    787:'Honedge',788:'Doublade',789:'Aegislash',790:'Spritzee',791:'Aromatisse',
    792:'Swirlix',793:'Slurpuff',794:'Inkay',795:'Malamar',796:'Binacle',
    797:'Barbaracle',798:'Skrelp',799:'Dragalge',800:'Clauncher',801:'Clawitzer',
    802:'Helioptile',803:'Heliolisk',804:'Tyrunt',805:'Tyrantrum',806:'Amaura',
    807:'Aurorus',808:'Sylveon',809:'Hawlucha',810:'Dedenne',811:'Carbink',
    812:'Goomy',813:'Sliggoo',814:'Goodra',815:'Klefki',816:'Phantump',
    817:'Trevenant',818:'Pumpkaboo',819:'Gourgeist',820:'Bergmite',821:'Avalugg',
    822:'Noibat',823:'Noivern',824:'Xerneas',825:'Yveltal',826:'Zygarde',
    827:'Diancie',828:'Hoopa',829:'Hoopa Unbound',830:'Volcanion',831:'Pyroar Female',
    832:'Meowstic Female',833:'Aegislash (Blade)',834:'Arceus (Fairy)',835:'Varoom',836:'Revavroom',
    837:'Zygarde 10',838:'Zygarde (Complete)',839:'Greninja (Ash)',840:'Tinkatink',841:'Tinkatuff',
    842:'Tinkaton',843:'Pawmi',844:'Pawmo',845:'Pawmot',846:'Lechonk',
    847:'Oinkologne',848:'Floette (Eternal)',849:'Oinkologne (Female)',850:'Gimmighoul (Chest)',851:'Gimmighoul (Roam)',
    852:'Gholdengo',853:'Pumpkaboo (Xl)',854:'Pumpkaboo (L)',855:'Pumpkaboo (M)',856:'Gourgeist (Xl)',
    857:'Gourgeist (L)',858:'Gourgeist (M)',859:'Greavard',860:'Houndstone',861:'Tadbulb',
    862:'Bellibolt',863:'Finizen',864:'Palafin',865:'Palafin (Hero)',866:'Noibat-Sevii',
    867:'Noivern-Sevii',868:'Farigiraf',869:'Venusaur (Mega)',870:'Charizard (Mega X)',871:'Charizard (Mega Y)',
    872:'Blastoise (Mega)',873:'Beedrill (Mega)',874:'Pidgeot (Mega)',875:'Alakazam (Mega)',876:'Slowbro (Mega)',
    877:'Gengar (Mega)',878:'Kangaskhan (Mega)',879:'Pinsir (Mega)',880:'Gyarados (Mega)',881:'Aerodactyl (Mega)',
    882:'Mewtwo (Mega X)',883:'Mewtwo (Mega Y)',884:'Ampharos (Mega)',885:'Steelix (Mega)',886:'Scizor (Mega)',
    887:'Heracross (Mega)',888:'Houndoom (Mega)',889:'Tyranitar (Mega)',890:'Sceptile (Mega)',891:'Blaziken (Mega)',
    892:'Swampert (Mega)',893:'Gardevoir (Mega)',894:'Sableye (Mega)',895:'Mawile (Mega)',896:'Aggron (Mega)',
    897:'Medicham (Mega)',898:'Manectric (Mega)',899:'Sharpedo (Mega)',900:'Camerupt (Mega)',901:'Altaria (Mega)',
    902:'Banette (Mega)',903:'Absol (Mega)',904:'Glalie (Mega)',905:'Salamence (Mega)',906:'Metagross (Mega)',
    907:'Latias (Mega)',908:'Latios (Mega)',909:'Groudon (Primal)',910:'Kyogre (Primal)',911:'Rayquaza (Mega)',
    912:'Lopunny (Mega)',913:'Garchomp (Mega)',914:'Lucario (Mega)',915:'Abomasnow (Mega)',916:'Gallade (Mega)',
    917:'Audino (Mega)',918:'Diancie (Mega)',919:'Dialga (Primal)',920:'Palkia (Primal)',921:'Sprigatito',
    922:'Floragato',923:'Meowscarada',924:'Fuecoco',925:'Crocalor',926:'Skeledirge',
    927:'Quaxly',928:'Quaxwell',929:'Quaquaval',930:'Wooper-Paldea',931:'Clodsire',
    932:'Wiglett',933:'Wugtrio',934:'Ceruledge',935:'Armarouge',936:'Kingambit',
    937:'Annihilape',938:'Charcadet',939:'Rowlet',940:'Dartrix',941:'Decidueye',
    942:'Litten',943:'Torracat',944:'Incineroar',945:'Popplio',946:'Brionne',
    947:'Primarina',948:'Pikipek',949:'Trumbeak',950:'Toucannon',951:'Yungoos',
    952:'Gumshoos',953:'Grubbin',954:'Charjabug',955:'Vikavolt',956:'Crabrawler',
    957:'Crabominable',958:'Oricorio',959:'Cutiefly',960:'Ribombee',961:'Rockruff',
    962:'Lycanroc',963:'Wishiwashi',964:'Mareanie',965:'Toxapex',966:'Mudbray',
    967:'Mudsdale',968:'Dewpider',969:'Araquanid',970:'Fomantis',971:'Lurantis',
    972:'Morelull',973:'Shiinotic',974:'Salandit',975:'Salazzle',976:'Stufful',
    977:'Bewear',978:'Bounsweet',979:'Steenee',980:'Tsareena',981:'Comfey',
    982:'Oranguru',983:'Passimian',984:'Wimpod',985:'Golisopod',986:'Sandygast',
    987:'Palossand',988:'Pyukumuku',989:'Type: Null',990:'Silvally',991:'Minior (Shield)',
    992:'Komala',993:'Turtonator',994:'Togedemaru',995:'Mimikyu',996:'Bruxish',
    997:'Drampa',998:'Dhelmise',999:'Jangmo-o',1000:'Hakamo-o',1001:'Kommo-o',
    1002:'Tapu Koko',1003:'Tapu Lele',1004:'Tapu Bulu',1005:'Tapu Fini',1006:'Cosmog',
    1007:'Cosmoem',1008:'Solgaleo',1009:'Lunala',1010:'Nihilego',1011:'Buzzwole',
    1012:'Pheromosa',1013:'Xurkitree',1014:'Celesteela',1015:'Kartana',1016:'Guzzlord',
    1017:'Necrozma',1018:'Magearna',1019:'Marshadow',1020:'Rattata-Alola',1021:'Raticate-Alola',
    1022:'Raichu-Alola',1023:'Sandshrew-Alola',1024:'Sandslash-Alola',1025:'Vulpix-Alola',1026:'Ninetales-Alola',
    1027:'Diglett-Alola',1028:'Dugtrio-Alola',1029:'Meowth-Alola',1030:'Persian-Alola',1031:'Geodude-Alola',
    1032:'Graveler-Alola',1033:'Golem-Alola',1034:'Grimer-Alola',1035:'Muk-Alola',1036:'Exeggcute-Alola',
    1037:'Exeggutor-Alola',1038:'Cubone-Alola',1039:'Marowak-Alola',1040:'Deoxys Attack',1041:'Deoxys Defense',
    1042:'Deoxys Speed',1043:'Oricorio Y',1044:'Oricorio-Paldea',1045:'Oricorio-Sevii',1046:'Lycanroc N',
    1047:'Wishiwashi-Sevii',1048:'Silvally (Fight)',1049:'Silvally (Flying)',1050:'Silvally (Poison)',1051:'Silvally (Ground)',
    1052:'Silvally (Rock)',1053:'Silvally (Bug)',1054:'Silvally (Ghost)',1055:'Silvally (Steel)',1056:'Silvally (Fire)',
    1057:'Silvally (Water)',1058:'Silvally (Grass)',1059:'Silvally (Electric)',1060:'Silvally (Psychic)',1061:'Silvally (Ice)',
    1062:'Silvally (Dragon)',1063:'Silvally (Dark)',1064:'Silvally (Fairy)',1065:'Minior (Red)',1066:'Minior (Blue)',
    1067:'Minior (Orange)',1068:'Minior (Yellow)',1069:'Minior (Indigo)',1070:'Minior (Green)',1071:'Minior (Violet)',
    1072:'Mimikyu (Busted)',1073:'Magearna-Paldea',1074:'Poipole',1075:'Naganadel',1076:'Stakataka',
    1077:'Blacephalon',1078:'Zeraora',1079:'Necrozma (Dusk Mane)',1080:'Necrozma (Dawn Wings)',1081:'Necrozma (Ultra)',
    1082:'Lycanroc (Dusk)',1083:'Meltan',1084:'Melmetal',1085:'Pikachu (Surfing)',1086:'Pikachu (Flying)',
    1087:'Pikachu (Cosplay)',1088:'Pikachu (Libre)',1089:'Pikachu (Pop Star)',1090:'Pikachu (Rock Star)',1091:'Pikachu (Belle)',
    1092:'Pikachu (Phd)',1093:'Pikachu (Cap Original)',1094:'Pikachu (Cap Hoenn)',1095:'Pikachu (Cap Sinnoh)',1096:'Pikachu (Cap Unova)',
    1097:'Pikachu (Cap Kalos)',1098:'Pikachu (Cap Alola)',1099:'Pikachu (Cap Partner)',1100:'Dudunsparce',1101:'Cyclizar',
    1102:'Grookey',1103:'Thwackey',1104:'Rillaboom',1105:'Scorbunny',1106:'Raboot',
    1107:'Cinderace',1108:'Sobble',1109:'Drizzile',1110:'Inteleon',1111:'Skwovet',
    1112:'Greedent',1113:'Rookidee',1114:'Corvisquire',1115:'Corviknight',1116:'Blipbug',
    1117:'Dottler',1118:'Orbeetle',1119:'Nickit',1120:'Thievul',1121:'Gossifleur',
    1122:'Eldegoss',1123:'Wooloo',1124:'Dubwool',1125:'Chewtle',1126:'Drednaw',
    1127:'Yamper',1128:'Boltund',1129:'Rolycoly',1130:'Carkol',1131:'Coalossal',
    1132:'Applin',1133:'Flapple',1134:'Appletun',1135:'Silicobra',1136:'Sandaconda',
    1137:'Cramorant',1138:'Arrokuda',1139:'Barraskewda',1140:'Toxel',1141:'Toxtricity',
    1142:'Sizzlipede',1143:'Centiskorch',1144:'Clobbopus',1145:'Grapploct',1146:'Sinistea',
    1147:'Polteageist',1148:'Hatenna',1149:'Hattrem',1150:'Hatterene',1151:'Impidimp',
    1152:'Morgrem',1153:'Grimmsnarl',1154:'Obstagoon',1155:'Perrserker',1156:'Cursola',
    1157:"Sirfetch'd",1158:'Mr. Rime',1159:'Runerigus',1160:'Milcery',1161:'Alcremie (Strawberry)',
    1162:'Falinks',1163:'Pincurchin',1164:'Snom',1165:'Frosmoth',1166:'Stonjourner',
    1167:'Eiscue',1168:'Indeedee',1169:'Morpeko',1170:'Cufant',1171:'Copperajah',
    1172:'Dracozolt',1173:'Arctozolt',1174:'Dracovish',1175:'Arctovish',1176:'Duraludon',
    1177:'Dreepy',1178:'Drakloak',1179:'Dragapult',1180:'Zacian',1181:'Zamazenta',
    1182:'Eternatus',1183:'Kubfu',1184:'Urshifu (Single)',1185:'Zarude',1186:'Lokix-Sevii',
    1187:'Calyrex',1188:'Regieleki',1189:'Regidrago',1190:'Cramorant (Gulping)',1191:'Cramorant (Gorging)',
    1192:'Toxtricity (Low Key)',1193:'Koraidon',1194:'Miraidon',1195:'Frigibax',1196:'Arctibax',
    1197:'Baxcalibur',1198:'Toedscool',1199:'Toedscruel',1200:'Nymble-Sevii',1201:'Eiscue (Noice)',
    1202:'Indeedee Female',1203:'Morpeko (Hangry)',1204:'Zacian (Crowned)',1205:'Zamazenta (Crowned)',1206:'Eternatus (Eternamax)',
    1207:'Urshifu (Rapid)',1208:'Meowth-Galar',1209:'Ponyta-Galar',1210:'Rapidash-Galar',1211:'Slowpoke-Galar',
    1212:'Slowbro-Galar',1213:"Farfetch'd-Galar",1214:'Koffing-Galar',1215:'Weezing-Galar',1216:'Mr Mime-Galar',
    1217:'Articuno-Galar',1218:'Zapdos-Galar',1219:'Moltres-Galar',1220:'Slowking-Galar',1221:'Corsola-Galar',
    1222:'Zigzagoon-Galar',1223:'Linoone-Galar',1224:'Mime Jr.-Galar',1225:'Darumaka-Galar',1226:'Darmanitan-Galar',
    1227:'Darmanitan G (Zen)',1228:'Yamask-Galar',1229:'Stunfisk-Galar',1230:'Ironthorns',1231:'Ironbundle',
    1232:'Ironvaliant',1233:'Butterfree (Giga)',1234:'Tauros-Paldea',1235:'Tauros P (Water)',1236:'Machamp (Giga)',
    1237:'Greattusk',1238:'Kingler (Giga)',1239:'Lapras (Giga)',1240:'Tauros P (Fire)',1241:'Snorlax (Giga)',
    1242:'Garbodor (Giga)',1243:'Brutebonnet',1244:'Sandyshocks',1245:'Screamtail',1246:'Fluttermane',
    1247:'Ironmoth',1248:'Orbeetle (Giga)',1249:'Drednaw (Giga)',1250:'Coalossal (Giga)',1251:'Flapple (Giga)',
    1252:'Appletun (Giga)',1253:'Sandaconda (Giga)',1254:'Toxtricity (Giga)',1255:'Slitherwing',1256:'Centiskorch (Giga)',
    1257:'Roaringmoon',1258:'Irontreads',1259:'Alcremie (Giga)',1260:'Copperajah (Giga)',1261:'Duraludon (Giga)',
    1262:'Ironhands',1263:'Ironjugulis',1264:'Calyrex (Ice)',1265:'Calyrex (Shadow)',1266:'Spectrier',
    1267:'Glastrier',1268:'Basculegion',1269:'Braviary-Hisui',1270:'Wyrdeer',1271:'Growlithe-Hisui',
    1272:'Arcanine-Hisui',1273:'Kleavor',1274:'Blitzle-Sevii',1275:'Zebstrika-Sevii',1276:'Doduo-Sevii',
    1277:'Dodrio-Sevii',1278:'Teddiursa-Sevii',1279:'Ursaring-Sevii',1280:'Zorua-Hisui',1281:'Zoroark-Hisui',
    1282:'Carnivine-Sevii',1283:'Mantyke-Sevii',1284:'Mantine-Sevii',1285:'Feebas-Sevii',1286:'Milotic-Sevii',
    1287:'Clauncher-Sevii',1288:'Clawitzer-Sevii',1289:'Sizzlipede-Sevii',1290:'Centiskorch-Sevii',1291:'Centiskorch S (Mega)',
    1292:'Wishiwashi Sevi',1293:'Wishiwashi Sevi-Sevii',1294:'Dhelmise-Sevii',1295:'Voltorb-Hisui',1296:'Electrode-Hisui',
    1297:'Sliggoo-Hisui',1298:'Goodra-Hisui',1299:'Typhlosion-Hisui',1300:'Samurott-Hisui',1301:'Decidueye-Hisui',
    1302:'Ursaluna',1303:'Lilligant-Hisui',1304:'Sneasel-Hisui',1305:'Sneasler',1306:'Basculegion (Female)',
    1307:'Qwilfish-Hisui',1308:'Overqwil',1309:'Avalugg-Hisui',1310:'Dialga (Origin)',1311:'Palkia (Origin)',
    1312:'Enamorus',1313:'Enamorus (Therian)',1314:'Tarountula',1315:'Spidops',1316:'Nymble',
    1317:'Lokix',1318:'Rellor',1319:'Rabsca',1320:'Flittle',1321:'Espathra',
    1322:'Dondozo',1323:'Veluza',1324:'Smoliv',1325:'Dolliv',1326:'Arboliva',
    1327:'Capsakid',1328:'Scovillain',1329:'Orthworm',1330:'Tandemaus',1331:'Maushold',
    1332:'Cetoddle',1333:'Cetitan',1334:'Tatsugiri',1335:'Wattrel',1336:'Kilowattrel',
    1337:'Bombirdier',1338:'Squawkabilly',1339:'Flamigo',1340:'Nacli',1341:'Naclstack',
    1342:'Garganacl',1343:'Glimmet',1344:'Glimmora',1345:'Shroodle',1346:'Grafaiai',
    1347:'Fidough',1348:'Dachsbun',1349:'Maschiff',1350:'Mabosstiff',1351:'Bramblin',
    1352:'Brambleghast',1353:'Ironleaves',1354:'Walkingwake',1355:'Squawkabilly 2',
}


# ── Gender ratio thresholds ─────────────────────────────────────────────────
# threshold 0=always-male, 254=always-female, 255=genderless.
# Otherwise: personality&0xFF < threshold → Female, else → Male.
# Default for unlisted species: 127 (50/50).
GENDER_RATIO: dict[int, int] = {
    1:31,2:31,3:31,4:31,5:31,6:31,7:31,8:31,9:31,29:254,
    30:254,31:254,32:0,33:0,34:0,35:191,36:191,37:191,38:191,39:191,
    40:191,58:63,59:63,63:63,64:63,65:63,66:63,67:63,68:63,81:255,
    82:255,100:255,101:255,106:0,107:0,113:254,115:254,120:255,121:255,124:254,
    125:63,126:63,128:0,132:255,133:31,134:31,135:31,136:31,137:255,138:31,
    139:31,140:31,141:31,142:31,143:31,144:255,145:255,146:255,147:31,148:31,
    149:31,150:255,151:255,152:31,153:31,154:31,155:31,156:31,157:31,158:31,
    159:31,160:31,173:191,174:191,175:31,176:31,196:31,197:31,201:255,
    209:191,210:191,222:191,233:255,
    236:0,237:0,238:254,239:63,240:63,241:254,242:254,243:255,244:255,245:255,
    246:31,247:31,248:31,249:255,250:255,251:255,277:31,278:31,279:31,280:31,
    281:31,282:31,283:31,284:31,285:31,315:191,316:191,318:255,319:255,346:191,
    347:191,348:255,349:255,386:0,387:254,388:31,389:31,390:31,391:31,395:31,
    396:31,397:31,398:255,399:255,400:255,401:255,402:255,403:255,404:255,405:255,
    406:255,407:254,408:0,409:255,410:255,440:31,441:31,442:31,443:31,444:31,
    445:31,446:31,447:31,448:31,461:31,462:31,463:31,464:31,466:254,467:0,
    468:254,469:254,489:255,490:255,493:254,499:31,500:31,501:31,515:255,519:63,
    520:63,521:31,523:31,524:31,527:255,528:0,531:254,532:255,533:255,534:255,
    535:255,536:255,537:255,539:255,540:255,541:254,542:255,543:255,544:255,545:255,
    546:255,547:255,548:31,549:31,550:31,551:31,552:31,553:31,554:31,555:31,
    556:31,591:0,592:0,601:254,602:254,617:31,618:31,619:31,620:31,652:255,
    653:255,654:255,668:255,675:255,676:255,680:0,681:0,691:255,692:255,693:255,
    696:255,697:255,699:255,700:255,701:255,702:255,758:31,759:31,760:31,761:31,
    762:31,763:31,764:31,765:31,766:31,777:254,778:254,779:254,804:31,805:31,
    806:31,807:31,808:31,811:255,824:255,825:255,826:255,827:255,828:255,830:255,
    939:31,940:31,941:31,942:31,943:31,944:31,945:31,946:31,947:31,975:254,
    978:254,979:254,980:254,989:255,990:255,998:255,1002:255,1003:255,1004:255,1005:255,
    1006:255,1007:255,1008:255,1009:255,1010:255,1011:255,1012:255,1013:255,1014:255,1015:255,
    1016:255,1017:255,1018:255,1019:255,1074:255,1075:255,1076:255,1077:255,1078:255,1083:255,
    1084:255,1102:31,1103:31,1104:31,1105:31,1106:31,1107:31,1108:31,1109:31,1110:31,
    1146:255,1147:255,1148:254,1149:254,1150:254,1151:0,1152:0,1153:0,1162:255,1172:255,
    1173:255,1174:255,1175:255,1180:255,1181:255,1182:255,1183:31,1185:255,1186:255,1187:255,
    1188:255,1189:255,1190:255,1258:255,1294:31,1295:31,1296:31,1297:31,1298:31,1299:31,
    1300:31,1301:31,1302:31,1350:254,1351:254,1352:254,
}


def gender_from_key_species(key_str: str, species_id: int, is_rr: bool = True) -> str:
    """Derive gender ('male'/'female'/'genderless') from the mon key and species.

    Personality is the first component of the key (PERSONALITY:OTID).
    Gender formula (Gen III): personality & 0xFF < threshold → female, else male.
    species_id is ALWAYS a CFRU internal ID (all ROM types use this).
    The is_rr parameter is kept for API compat but no longer changes behavior.
    """
    if not key_str or not species_id:
        return ""
    try:
        personality = int(key_str.split(":")[0], 16)
    except (ValueError, IndexError):
        return ""
    threshold = GENDER_RATIO.get(species_id, 127)
    if threshold == 255:
        return "genderless"
    if threshold == 254:
        return "female"
    if threshold == 0:
        return "male"
    return "female" if (personality & 0xFF) < threshold else "male"


GENDER_SYMBOL = {"male": "♂", "female": "♀", "genderless": ""}


# ── Evolution families — evolved species → base-form species ID ─────────────
# Single-stage mons are NOT listed — base_form() returns the species
# itself for any missing key.  Split evos share the same base form.
# Uses CFRU internal IDs.
EVO_FAMILY: dict[int, int] = {
    2:1,3:1,5:4,6:4,8:7,9:7,11:10,12:10,14:13,15:13,
    17:16,18:16,20:19,22:21,24:23,25:172,26:172,28:27,30:29,31:29,
    33:32,34:32,35:173,36:173,38:37,39:174,40:174,42:41,44:43,45:43,
    47:46,49:48,51:50,53:52,55:54,57:56,59:58,61:60,62:60,64:63,
    65:63,67:66,68:66,70:69,71:69,73:72,75:74,76:74,78:77,80:79,
    82:81,85:84,87:86,89:88,91:90,93:92,94:92,97:96,99:98,101:100,
    103:102,105:104,106:236,107:236,110:109,112:111,113:493,117:116,119:118,121:120,
    122:492,124:238,125:239,126:240,130:129,134:133,135:133,136:133,139:138,141:140,
    143:499,148:147,149:147,153:152,154:152,156:155,157:155,159:158,160:158,162:161,164:163,
    166:165,168:167,169:41,171:170,176:175,178:177,180:179,181:179,182:43,183:350,
    184:350,185:491,186:60,188:187,189:187,192:191,195:194,196:133,197:133,199:79,
    202:360,205:204,208:95,210:209,212:123,217:216,219:218,221:220,224:223,226:511,
    229:228,230:116,232:231,233:137,237:236,242:493,247:246,248:246,278:277,279:277,
    281:280,282:280,284:283,285:283,287:286,289:288,291:290,292:290,293:290,294:290,
    296:295,297:295,299:298,300:298,302:301,303:301,305:304,307:306,310:309,312:311,
    314:313,316:315,319:318,324:323,327:326,329:328,331:330,333:332,334:332,336:335,
    338:337,340:339,342:341,343:341,345:344,347:346,352:351,357:356,359:358,362:361,
    363:459,365:364,366:364,368:367,371:370,372:370,374:373,375:373,378:377,383:382,
    384:382,389:388,391:390,393:392,394:392,396:395,397:395,399:398,400:398,411:486,
    441:440,442:440,444:443,445:443,447:446,448:446,450:449,451:449,453:452,455:454,
    457:456,458:456,460:459,462:461,464:463,466:465,467:465,469:468,472:471,474:473,
    476:475,477:190,479:478,481:480,482:200,483:198,485:484,488:487,497:496,498:496,
    501:500,503:502,505:504,507:506,510:509,513:512,514:215,515:81,516:108,517:111,
    518:114,519:239,520:240,521:175,523:133,524:133,525:207,526:220,527:137,528:392,
    529:320,530:361,531:346,549:548,550:548,552:551,553:551,555:554,556:554,558:557,
    560:559,561:559,563:562,565:564,567:566,569:568,571:570,573:572,574:572,576:575,
    578:577,581:580,583:582,586:585,587:585,589:588,590:588,594:593,595:593,597:596,
    598:596,600:599,602:601,605:604,606:604,608:607,611:610,613:612,616:615,618:617,
    620:619,622:621,624:623,626:625,628:627,629:627,631:630,632:630,634:633,636:635,
    637:635,642:641,644:643,646:645,649:648,651:650,653:652,654:652,656:655,657:655,
    659:658,661:660,662:660,664:663,665:663,667:666,670:669,676:675,678:677,681:680,
    683:682,687:686,688:686,690:689,759:758,760:758,762:761,763:761,765:764,766:764,
    768:767,770:769,771:769,773:772,774:772,776:775,778:777,779:777,781:780,783:782,
    786:785,788:787,789:787,795:794,797:796,799:798,801:800,803:802,805:804,807:806,
    808:133,813:812,814:812,817:816,819:818,821:820,823:822,836:835,841:840,842:840,
    844:843,845:843,847:846,849:846,852:850,860:859,862:861,864:863,865:863,868:203,
    922:921,923:921,925:924,926:924,928:927,929:927,931:930,933:932,934:938,935:938,
    936:677,937:56,940:939,941:939,943:942,944:942,946:945,947:945,949:948,950:948,
    952:951,954:953,955:953,957:956,960:959,962:961,965:964,967:966,969:968,971:970,
    973:972,975:974,977:976,979:978,980:978,985:984,987:986,990:989,1000:999,1001:999,
    1007:1006,1008:1006,1009:1006,1075:1074,1100:206,1103:1102,1104:1102,1106:1105,1107:1105,1109:1108,
    1110:1108,1124:1123,1126:1125,1128:1127,1130:1129,1131:1129,1133:1132,1134:1132,1136:1135,1139:1138,
    1141:1140,1143:1142,1145:1144,1147:1146,1149:1148,1150:1148,1152:1151,1153:1151,1154:288,1155:52,
    1156:222,1158:492,1159:615,1171:1170,1178:1177,1179:1177,1186:1200,1196:1195,1197:1195,1199:1198,
    1269:680,1272:1271,1275:1274,1277:1276,1279:1278,1281:1280,1284:1283,1286:1285,1288:1287,1290:1289,
    1291:1289,1293:1292,1296:1295,1298:1297,1299:155,1300:554,1301:939,1302:216,1303:601,1305:1304,
    1306:1268,1308:1307,1309:820,1313:1312,1315:1314,1317:1316,1319:1318,1321:1320,1325:1324,1326:1324,
    1328:1327,1331:1330,1333:1332,1336:1335,1341:1340,1342:1340,1344:1343,1346:1345,1348:1347,1350:1349,
    1352:1351,1355:1338,
}


# ── CFRU internal species ID → National Pokédex number ──────────────────────
# Only non-identity mappings (where CFRU ID != NatDex).
# Gen 1–2 (1–251) are identity and not listed.
# Use to_national() for sprite URLs and display.
CFRU_TO_NATIONAL: dict[int, int] = {
    277:252,278:253,279:254,280:255,281:256,282:257,283:258,284:259,
    285:260,286:261,287:262,288:263,289:264,290:265,291:266,292:267,
    293:268,294:269,295:270,296:271,297:272,298:273,299:274,300:275,
    301:290,302:291,303:292,304:276,305:277,306:285,307:286,308:327,
    309:278,310:279,311:283,312:284,313:320,314:321,315:300,316:301,
    317:352,318:343,319:344,320:299,321:324,322:302,323:339,324:340,
    325:370,326:341,327:342,328:349,329:350,330:318,331:319,332:328,
    333:329,334:330,335:296,336:297,337:309,338:310,339:322,340:323,
    341:363,342:364,343:365,344:331,345:332,346:361,347:362,348:337,
    349:338,350:298,351:325,352:326,353:311,354:312,355:303,356:307,
    357:308,358:333,359:334,361:355,362:356,363:315,364:287,365:288,
    366:289,367:316,368:317,369:357,370:293,371:294,372:295,373:366,
    374:367,375:368,376:359,377:353,378:354,379:336,380:335,381:369,
    382:304,383:305,384:306,385:351,386:313,387:314,388:345,389:346,
    390:347,391:348,392:280,393:281,394:282,395:371,396:372,397:373,
    398:374,399:375,400:376,401:377,402:378,403:379,404:382,405:383,
    406:384,407:380,408:381,409:385,410:386,411:358,413:201,414:201,
    415:201,416:201,417:201,418:201,419:201,420:201,421:201,422:201,
    423:201,424:201,425:201,426:201,427:201,428:201,429:201,430:201,
    431:201,432:201,433:201,434:201,435:201,436:201,437:201,438:201,
    439:201,440:387,441:388,442:389,443:390,444:391,445:392,446:393,
    447:394,448:395,449:396,450:397,451:398,452:399,453:400,454:401,
    455:402,456:403,457:404,458:405,459:406,460:407,461:408,462:409,
    463:410,464:411,465:412,466:413,467:414,468:415,469:416,470:417,
    471:418,472:419,473:420,474:421,475:422,476:423,477:424,478:425,
    479:426,480:427,481:428,482:429,483:430,484:431,485:432,486:433,
    487:434,488:435,489:436,490:437,491:438,492:439,493:440,494:441,
    495:442,496:443,497:444,498:445,499:446,500:447,501:448,502:449,
    503:450,504:451,505:452,506:453,507:454,508:455,509:456,510:457,
    511:458,512:459,513:460,514:461,515:462,516:463,517:464,518:465,
    519:466,520:467,521:468,522:469,523:470,524:471,525:472,526:473,
    527:474,528:475,529:476,530:477,531:478,532:479,533:480,534:481,
    535:482,536:483,537:484,538:485,539:486,540:487,541:488,542:489,
    543:490,544:491,545:492,546:493,547:494,548:495,549:496,550:497,
    551:498,552:499,553:500,554:501,555:502,556:503,557:504,558:505,
    559:506,560:507,561:508,562:509,563:510,564:511,565:512,566:513,
    567:514,568:515,569:516,570:517,571:518,572:519,573:520,574:521,
    575:522,576:523,577:524,578:525,579:526,580:527,581:528,582:529,
    583:530,584:531,585:532,586:533,587:534,588:535,589:536,590:537,
    591:538,592:539,593:540,594:541,595:542,596:543,597:544,598:545,
    599:546,600:547,601:548,602:549,603:550,604:551,605:552,606:553,
    607:554,608:555,609:556,610:557,611:558,612:559,613:560,614:561,
    615:562,616:563,617:564,618:565,619:566,620:567,621:568,622:569,
    623:570,624:571,625:572,626:573,627:574,628:575,629:576,630:577,
    631:578,632:579,633:580,634:581,635:582,636:583,637:584,638:585,
    639:586,640:587,641:588,642:589,643:590,644:591,645:592,646:593,
    647:594,648:595,649:596,650:597,651:598,652:599,653:600,654:601,
    655:602,656:603,657:604,658:605,659:606,660:607,661:608,662:609,
    663:610,664:611,665:612,666:613,667:614,668:615,669:616,670:617,
    671:618,672:619,673:620,674:621,675:622,676:623,677:624,678:625,
    679:626,680:627,681:628,682:629,683:630,684:631,685:632,686:633,
    687:634,688:635,689:636,690:637,691:638,692:639,693:640,694:641,
    695:642,696:643,697:644,698:645,699:646,700:647,701:648,702:649,
    703:521,704:592,705:593,706:950,707:412,708:412,709:413,710:413,
    711:422,712:423,713:479,714:479,715:479,716:479,717:479,718:487,
    719:492,720:493,721:493,722:493,723:493,724:493,725:493,726:493,
    727:493,728:493,729:493,730:493,731:493,732:493,733:493,734:493,
    735:493,736:550,737:555,738:585,739:585,740:585,741:586,742:586,
    743:586,744:449,745:450,746:648,747:1003,748:1001,749:1000,750:1004,
    751:421,752:646,753:646,754:641,755:642,756:645,757:647,758:650,
    759:651,760:652,761:653,762:654,763:655,764:656,765:657,766:658,
    767:659,768:660,769:661,770:662,771:663,772:664,773:665,774:666,
    775:667,776:668,777:669,778:670,779:671,780:672,781:673,782:674,
    783:675,784:676,785:677,786:678,787:679,788:680,789:681,790:682,
    791:683,792:684,793:685,794:686,795:687,796:688,797:689,798:690,
    799:691,800:692,801:693,802:694,803:695,804:696,805:697,806:698,
    807:699,808:700,809:701,810:702,811:703,812:704,813:705,814:706,
    815:707,816:708,817:709,818:710,819:711,820:712,821:713,822:714,
    823:715,824:716,825:717,826:718,827:719,828:720,829:720,830:721,
    831:668,832:678,833:681,834:493,835:965,836:966,837:718,838:718,
    839:658,840:957,841:958,842:959,843:921,844:922,845:923,846:915,
    847:916,848:670,849:916,850:999,851:999,852:1000,853:710,854:710,
    855:710,856:711,857:711,858:711,859:971,860:972,861:938,862:939,
    863:963,864:964,865:964,866:714,867:715,868:981,869:3,870:6,
    871:6,872:9,873:15,874:18,875:65,876:80,877:94,878:115,
    879:127,880:130,881:142,882:150,883:150,884:181,885:208,886:212,
    887:214,888:229,889:248,890:254,891:257,892:260,893:282,894:302,
    895:303,896:306,897:308,898:310,899:319,900:323,901:334,902:354,
    903:359,904:362,905:373,906:376,907:380,908:381,909:383,910:382,
    911:384,912:428,913:445,914:448,915:460,916:475,917:531,918:719,
    919:483,920:484,921:906,922:907,923:908,924:909,925:910,926:911,
    927:912,928:913,929:914,930:194,931:980,932:960,933:961,934:937,
    935:936,936:983,937:979,938:935,939:722,940:723,941:724,942:725,
    943:726,944:727,945:728,946:729,947:730,948:731,949:732,950:733,
    951:734,952:735,953:736,954:737,955:738,956:739,957:740,958:741,
    959:742,960:743,961:744,962:745,963:746,964:747,965:748,966:749,
    967:750,968:751,969:752,970:753,971:754,972:755,973:756,974:757,
    975:758,976:759,977:760,978:761,979:762,980:763,981:764,982:765,
    983:766,984:767,985:768,986:769,987:770,988:771,989:772,990:773,
    991:774,992:775,993:776,994:777,995:778,996:779,997:780,998:781,
    999:782,1000:783,1001:784,1002:785,1003:786,1004:787,1005:788,1006:789,
    1007:790,1008:791,1009:792,1010:793,1011:794,1012:795,1013:796,1014:797,
    1015:798,1016:799,1017:800,1018:801,1019:802,1020:19,1021:20,1022:26,
    1023:27,1024:28,1025:37,1026:38,1027:50,1028:51,1029:52,1030:53,
    1031:74,1032:75,1033:76,1034:88,1035:89,1036:102,1037:103,1038:104,
    1039:105,1040:386,1041:386,1042:386,1043:741,1044:741,1045:741,1046:745,
    1047:746,1048:773,1049:773,1050:773,1051:773,1052:773,1053:773,1054:773,
    1055:773,1056:773,1057:773,1058:773,1059:773,1060:773,1061:773,1062:773,
    1063:773,1064:773,1065:774,1066:774,1067:774,1068:774,1069:774,1070:774,
    1071:774,1072:778,1073:801,1074:803,1075:804,1076:805,1077:806,1078:807,
    1079:800,1080:800,1081:800,1082:745,1083:808,1084:809,1085:25,1086:25,
    1087:25,1088:25,1089:25,1090:25,1091:25,1092:25,1093:25,1094:25,
    1095:25,1096:25,1097:25,1098:25,1099:25,1100:982,1101:967,1102:810,
    1103:811,1104:812,1105:813,1106:814,1107:815,1108:816,1109:817,1110:818,
    1111:819,1112:820,1113:821,1114:822,1115:823,1116:824,1117:825,1118:826,
    1119:827,1120:828,1121:829,1122:830,1123:831,1124:832,1125:833,1126:834,
    1127:835,1128:836,1129:837,1130:838,1131:839,1132:840,1133:841,1134:842,
    1135:843,1136:844,1137:845,1138:846,1139:847,1140:848,1141:849,1142:850,
    1143:851,1144:852,1145:853,1146:854,1147:855,1148:856,1149:857,1150:858,
    1151:859,1152:860,1153:861,1154:862,1155:863,1156:864,1157:865,1158:866,
    1159:867,1160:868,1161:869,1162:870,1163:871,1164:872,1165:873,1166:874,
    1167:875,1168:876,1169:877,1170:878,1171:879,1172:880,1173:881,1174:882,
    1175:883,1176:884,1177:885,1178:886,1179:887,1180:888,1181:889,1182:890,
    1183:891,1184:892,1185:893,1186:920,1187:898,1188:894,1189:895,1190:845,
    1191:845,1192:849,1193:1007,1194:1008,1195:997,1196:998,1197:999,1198:948,
    1199:949,1200:919,1201:875,1202:876,1203:877,1204:888,1205:889,1206:890,
    1207:892,1208:52,1209:77,1210:78,1211:79,1212:80,1213:83,1214:109,
    1215:110,1216:122,1217:144,1218:145,1219:146,1220:199,1221:222,1222:263,
    1223:264,1224:439,1225:554,1226:555,1227:555,1228:562,1229:618,1230:996,
    1231:991,1232:995,1233:12,1234:128,1235:128,1236:68,1237:984,1238:99,
    1239:131,1240:128,1241:143,1242:569,1243:986,1244:989,1245:985,1246:987,
    1247:994,1248:826,1249:834,1250:839,1251:841,1252:842,1253:844,1254:849,
    1255:988,1256:851,1257:1005,1258:990,1259:869,1260:879,1261:884,1262:992,
    1263:993,1264:898,1265:898,1266:896,1267:897,1268:902,1269:628,1270:899,
    1271:58,1272:59,1273:900,1274:522,1275:523,1276:84,1277:85,1278:216,
    1279:217,1280:570,1281:571,1282:455,1283:458,1284:226,1285:349,1286:350,
    1287:692,1288:693,1289:850,1290:851,1291:851,1292:746,1293:746,1294:781,
    1295:100,1296:101,1297:705,1298:706,1299:157,1300:503,1301:724,1302:901,
    1303:549,1304:215,1305:903,1306:902,1307:211,1308:904,1309:713,1310:483,
    1311:484,1312:905,1313:905,1314:917,1315:918,1316:919,1317:920,1318:953,
    1319:954,1320:955,1321:956,1322:977,1323:976,1324:928,1325:929,1326:930,
    1327:951,1328:952,1329:968,1330:924,1331:925,1332:974,1333:975,1334:978,
    1335:940,1336:941,1337:962,1338:931,1339:973,1340:932,1341:933,1342:934,
    1343:969,1344:970,1345:944,1346:945,1347:926,1348:927,1349:942,1350:943,
    1351:946,1352:947,1353:1010,1354:1009,1355:931,
}


def to_national(cfru_id: int) -> int:
    """Convert a CFRU/FRLG internal species ID to National Pokédex number.

    Gen 1–2 IDs (1–251) pass through unchanged.
    Gen 3+ CFRU IDs are mapped via CFRU_TO_NATIONAL.
    Returns the input ID if no mapping exists.
    """
    if cfru_id <= 251:
        return cfru_id
    return CFRU_TO_NATIONAL.get(cfru_id, cfru_id)


# ── National Dex species names — built from SPECIES_NAMES + CFRU_TO_NATIONAL ──
# For vanilla / AP clients that send NatDex IDs instead of CFRU IDs.
NATIONAL_SPECIES_NAMES: dict[int, str] = {}
for _cfru_id, _name in SPECIES_NAMES.items():
    _nat = to_national(_cfru_id)
    # Prefer the first mapping (avoids Unown forms overwriting each other).
    if _nat not in NATIONAL_SPECIES_NAMES:
        NATIONAL_SPECIES_NAMES[_nat] = _name
del _cfru_id, _name, _nat


def _rr_species_names() -> dict[int, str]:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data/games/gen3_frlge/rr_species.json"
    if not path.exists():
        return {}
    return {int(sid): name for sid, name in json.loads(path.read_text(encoding="utf-8")).items()}


_RR_SPECIES_NAMES = _rr_species_names()


def species_name(species_id: int, is_rr: bool = False) -> str:
    """Look up a species display name by ID.

    species_id is ALWAYS a CFRU internal ID (all ROM types use this).
    RR uses its generated catalog; generic/NatDex names retain the existing map.
    """
    if is_rr and species_id in _RR_SPECIES_NAMES:
        return _RR_SPECIES_NAMES[species_id]
    return SPECIES_NAMES.get(species_id, f"#{species_id}")


# ── National Dex → CFRU reverse mapping (for vanilla/AP IDs) ────────────────
# Only non-identity entries (Gen 1–2 are identity).
NATIONAL_TO_CFRU: dict[int, int] = {}
for _cfru_id2, _nat_id2 in CFRU_TO_NATIONAL.items():
    # Prefer the lowest CFRU ID for each NatDex (avoids alt-form collisions).
    if _nat_id2 not in NATIONAL_TO_CFRU or _cfru_id2 < NATIONAL_TO_CFRU[_nat_id2]:
        NATIONAL_TO_CFRU[_nat_id2] = _cfru_id2
del _cfru_id2, _nat_id2


def to_cfru(natdex_id: int) -> int:
    """Convert a National Pokédex number to CFRU internal species ID.

    Gen 1–2 IDs (1–251) pass through unchanged.
    Returns the input if no reverse mapping exists.
    """
    if natdex_id <= 251:
        return natdex_id
    return NATIONAL_TO_CFRU.get(natdex_id, natdex_id)


# ── Ability ID → display name ───────────────────────────────────────────────
# CFRU renumbers a handful of vanilla Gen III ability IDs (72-74, 76) to
# make room for Gen 8+ abilities.  The main table uses CFRU numbering;
# vanilla overrides are applied server-side when rom_type is not RR.
# Generated from Complete-Fire-Red-Upgrade/include/constants/abilities.h
ABILITY_NAMES: dict[int, str] = {
    0: "None",
    1: "Stench", 2: "Drizzle", 3: "Speed Boost", 4: "Battle Armor",
    5: "Sturdy", 6: "Damp", 7: "Limber", 8: "Sand Veil",
    9: "Static", 10: "Volt Absorb", 11: "Water Absorb", 12: "Oblivious",
    13: "Cloud Nine", 14: "Compound Eyes", 15: "Insomnia", 16: "Color Change",
    17: "Immunity", 18: "Flash Fire", 19: "Shield Dust", 20: "Own Tempo",
    21: "Suction Cups", 22: "Intimidate", 23: "Shadow Tag", 24: "Rough Skin",
    25: "Wonder Guard", 26: "Levitate", 27: "Effect Spore", 28: "Synchronize",
    29: "Clear Body", 30: "Natural Cure", 31: "Lightning Rod", 32: "Serene Grace",
    33: "Swift Swim", 34: "Chlorophyll", 35: "Illuminate", 36: "Trace",
    37: "Huge Power", 38: "Poison Point", 39: "Inner Focus", 40: "Magma Armor",
    41: "Water Veil", 42: "Magnet Pull", 43: "Soundproof", 44: "Rain Dish",
    45: "Sand Stream", 46: "Pressure", 47: "Thick Fat", 48: "Early Bird",
    49: "Flame Body", 50: "Run Away", 51: "Keen Eye", 52: "Hyper Cutter",
    53: "Pickup", 54: "Truant", 55: "Hustle", 56: "Cute Charm",
    57: "Plus", 58: "Minus", 59: "Forecast", 60: "Sticky Hold",
    61: "Shed Skin", 62: "Guts", 63: "Marvel Scale", 64: "Liquid Ooze",
    65: "Overgrow", 66: "Blaze", 67: "Torrent", 68: "Swarm",
    69: "Rock Head", 70: "Drought", 71: "Arena Trap",
    # 72-76: CFRU replaces vanilla abilities; vanilla overrides applied separately
    72: "Transistor", 73: "Dragon's Maw", 74: "Neutralizing Gas",
    75: "Shell Armor", 76: "Hunger Switch", 77: "Unused",
    78: "Sweet Veil", 79: "Skill Link", 80: "Motor Drive", 81: "Multiscale",
    82: "Technician", 83: "Scrappy", 84: "Super Luck", 85: "Sniper",
    86: "Regenerator", 87: "Prankster", 88: "Adaptability", 89: "Big Pecks",
    90: "Magic Bounce", 91: "Reckless", 92: "Sheer Force", 93: "Iron Fist",
    94: "Rivalry", 95: "Sand Force", 96: "Solar Power", 97: "Heatproof",
    98: "Dry Skin", 99: "Tinted Lens", 100: "Unseen Fist", 101: "Filter",
    102: "Infiltrator", 103: "Download", 104: "Poison Heal", 105: "Ice Body",
    106: "Moody", 107: "Hydration", 108: "Healer", 109: "Snow Cloak",
    110: "Tangled Feet", 111: "Snow Warning", 112: "Quick Feet", 113: "Sap Sipper",
    114: "Overcoat", 115: "Magic Guard", 116: "Bulletproof", 117: "Gale Wings",
    118: "Moxie", 119: "Aftermath", 120: "Cursed Body", 121: "Gooey",
    122: "Mummy", 123: "Grim Neigh", 124: "Sand Rush", 125: "Analytic",
    126: "No Guard", 127: "Mega Launcher", 128: "Tough Claws", 129: "Strong Jaw",
    130: "Victory Star", 131: "Storm Drain", 132: "Dark Aura", 133: "Fairy Aura",
    134: "Aura Break", 135: "Refrigerate", 136: "Pixilate", 137: "Aerilate",
    138: "Normalize", 139: "Unburden", 140: "Simple", 141: "Unaware",
    142: "Defiant", 143: "Competitive", 144: "Defeatist", 145: "Slow Start",
    146: "Toxic Boost", 147: "Flare Boost", 148: "Fur Coat", 149: "Wonder Skin",
    150: "Protean", 151: "Parental Bond", 152: "Mold Breaker",
    153: "As One (Grim)", 154: "As One (Chilling)",
    155: "Zen Mode", 156: "Battle Bond", 157: "Beast Boost", 158: "Corrosion",
    159: "Disguise", 160: "Emergency Exit", 161: "Fluffy", 162: "Steely Spirit",
    163: "Perish Body", 164: "Wandering Spirit", 165: "Power Construct",
    166: "Prism Armor", 167: "RKS System", 168: "Schooling", 169: "Shadow Shield",
    170: "Shields Down", 171: "Slush Rush", 172: "Soul-Heart", 173: "Stamina",
    174: "Steelworker", 175: "Full Metal Body", 176: "Triage",
    177: "Water Bubble", 178: "Water Compaction", 179: "Stall", 180: "Multitype",
    181: "Electric Surge", 182: "Grassy Surge", 183: "Misty Surge",
    184: "Psychic Surge", 185: "Surge Surfer", 186: "Grass Pelt",
    187: "Anger Point", 188: "Anticipation", 189: "Forewarn", 190: "Frisk",
    191: "Contrary", 192: "Unnerve", 193: "Weak Armor", 194: "Heavy Metal",
    195: "Light Metal", 196: "Steadfast", 197: "Imposter", 198: "Justified",
    199: "Rattled", 200: "Merciless", 201: "Aroma Veil", 202: "Flower Veil",
    203: "Leaf Guard", 204: "Klutz", 205: "Flower Gift", 206: "Bad Dreams",
    207: "Pickpocket", 208: "Portal Power", 209: "Poison Touch", 210: "Magician",
    211: "Stance Change", 212: "Primordial Sea", 213: "Desolate Land",
    214: "Delta Stream", 215: "Gorilla Tactics", 216: "Berserk", 217: "Long Reach",
    218: "Liquid Voice", 219: "Quick Draw", 220: "Innards Out", 221: "Dazzling",
    222: "Gluttony", 223: "Honey Gather", 224: "Friend Guard", 225: "Harvest",
    226: "Telepathy", 227: "Illusion", 228: "Cheek Pouch", 229: "Symbiosis",
    230: "Stakeout", 231: "Comatose", 232: "Dancer", 233: "Battery",
    234: "Receiver", 235: "Curious Medicine", 236: "Neuroforce", 237: "Galvanize",
    238: "Intrepid Sword", 239: "Dauntless Shield", 240: "Ball Fetch",
    241: "Cotton Down", 242: "Mirror Armor", 243: "Gulp Missile", 244: "Stalwart",
    245: "Steam Engine", 246: "Punk Rock", 247: "Sand Spit", 248: "Ice Scales",
    249: "Ripen", 250: "Ice Face", 251: "Power Spot", 252: "Mimicry",
    253: "Screen Cleaner", 254: "Pastel Veil",
}

# RR-specific ability names (generated from funnotbun/funnotbun.github.io)
# Regenerate with: python tools/gen_ability_names_rr.py
RR_ABILITY_NAMES: dict[int, str] = {
    1: "Stench",
    2: "Drizzle",
    3: "Speed Boost",
    4: "Battle Armor",
    5: "Sturdy",
    6: "Damp",
    7: "Limber",
    8: "Sand Veil",
    9: "Static",
    10: "Volt Absorb",
    11: "Water Absorb",
    12: "Oblivious",
    13: "Cloud Nine",
    14: "Compound Eyes",
    15: "Insomnia",
    16: "Wind Rider",
    17: "Immunity",
    18: "Flash Fire",
    19: "Shield Dust",
    20: "Own Tempo",
    21: "Suction Cups",
    22: "Intimidate",
    23: "Shadow Tag",
    24: "Quick Draw",
    25: "Wonder Guard",
    26: "Levitate",
    27: "Effect Spore",
    28: "Synchronize",
    29: "Clear Body",
    30: "Natural Cure",
    31: "Lightning Rod",
    32: "Serene Grace",
    33: "Swift Swim",
    34: "Chlorophyll",
    35: "Gulp Missile",
    36: "Trace",
    37: "Huge Power",
    38: "Dragon's Maw",
    39: "Inner Focus",
    40: "Magma Armor",
    41: "Water Veil",
    42: "Magnet Pull",
    43: "Soundproof",
    44: "Rain Dish",
    45: "Sand Stream",
    46: "Pressure",
    47: "Thick Fat",
    48: "Early Bird",
    49: "Flame Body",
    50: "Run Away",
    51: "Bad Company",
    52: "Hyper Cutter",
    53: "Pickup",
    54: "Truant",
    55: "Hustle",
    56: "Cute Charm",
    57: "Plus",
    58: "Minus",
    59: "Forecast",
    60: "Sticky Hold",
    61: "Shed Skin",
    62: "Guts",
    63: "Marvel Scale",
    64: "Liquid Ooze",
    65: "Overgrow",
    66: "Blaze",
    67: "Torrent",
    68: "Swarm",
    69: "Rock Head",
    70: "Drought",
    71: "Arena Trap",
    72: "Purifying Salt",
    73: "As One",
    74: "Neutralize Gas",
    75: "Fatal Precision",
    76: "Hunger Switch",
    77: "As One",
    78: "Sweet Veil",
    79: "Skill Link",
    80: "Motor Drive",
    81: "Multiscale",
    82: "Technician",
    83: "Scrappy",
    84: "Super Luck",
    85: "Sniper",
    86: "Regenerator",
    87: "Prankster",
    88: "Adaptability",
    89: "Illuminate",
    90: "Magic Bounce",
    91: "Reckless",
    92: "Sheer Force",
    93: "Iron Fist",
    94: "Rivalry",
    95: "Sand Force",
    96: "Solar Power",
    97: "Heatproof",
    98: "Dry Skin",
    99: "Tinted Lens",
    100: "Solid Rock",
    101: "Stalwart",
    102: "Infiltrator",
    103: "Download",
    104: "Poison Heal",
    105: "Ice Body",
    106: "Bull Rush",
    107: "Hydration",
    108: "SupremeOverlord",
    109: "Anger Shell",
    110: "Good as Gold",
    111: "Snow Warning",
    112: "Quick Feet",
    113: "Sap Sipper",
    114: "Overcoat",
    115: "Magic Guard",
    116: "Bulletproof",
    117: "Gale Wings",
    118: "Moxie",
    119: "Aftermath",
    120: "Cursed Body",
    121: "Gooey",
    122: "Mummy",
    123: "Iron Barbs",
    124: "Sand Rush",
    125: "Analytic",
    126: "No Guard",
    127: "Mega Launcher",
    128: "Tough Claws",
    129: "Strong Jaw",
    130: "Victory Star",
    131: "Storm Drain",
    132: "Dark Aura",
    133: "Fairy Aura",
    134: "Seed Sower",
    135: "Refrigerate",
    136: "Pixilate",
    137: "Aerilate",
    138: "Feline Prowess",
    139: "Unburden",
    140: "Simple",
    141: "Unaware",
    142: "Defiant",
    143: "Competitive",
    144: "Defeatist",
    145: "Slow Start",
    146: "Toxic Boost",
    147: "Flare Boost",
    148: "Fur Coat",
    149: "Wonder Skin",
    150: "Protean",
    151: "Parental Bond",
    152: "Mold Breaker",
    153: "Hadron Engine",
    154: "OrichalcumPulse",
    155: "Zen Mode",
    156: "Battle Bond",
    157: "Beast Boost",
    158: "Corrosion",
    159: "Disguise",
    160: "Emergency Exit",
    161: "Fluffy",
    162: "Steely Spirit",
    163: "Perish Body",
    164: "Wandering Soul",
    165: "Power Construct",
    166: "Tablets of Ruin",
    167: "ORAORAORAORA!",
    168: "Schooling",
    169: "Beads of Ruin",
    170: "Shields Down",
    171: "Slush Rush",
    172: "Soul-Heart",
    173: "Stamina",
    174: "Zero To Hero",
    175: "ThermalExchange",
    176: "Triage",
    177: "Water Bubble",
    178: "WaterCompaction",
    179: "Parasitic Waste",
    180: "Multitype",
    181: "Electric Surge",
    182: "Grassy Surge",
    183: "Misty Surge",
    184: "Psychic Surge",
    185: "Surge Surfer",
    186: "Grass Pelt",
    187: "Anger Point",
    188: "Earth Eater",
    189: "Forewarn",
    190: "Frisk",
    191: "Contrary",
    192: "Unnerve",
    193: "Weak Armor",
    194: "Heavy Metal",
    195: "Quark Drive",
    196: "Steadfast",
    197: "Imposter",
    198: "Justified",
    199: "Unseen Fist",
    200: "Merciless",
    201: "Aroma Veil",
    202: "Toxic Debris",
    203: "Leaf Guard",
    204: "Electromrphosis",
    205: "Flower Gift",
    206: "Bad Dreams",
    207: "Grim Neigh",
    208: "Transistor",
    209: "Poison Touch",
    210: "Magician",
    211: "Stance Change",
    212: "Primordial Sea",
    213: "Desolate Land",
    214: "Delta Stream",
    215: "Gorilla Tactics",
    216: "Berserk",
    217: "Primal Armor",
    218: "Liquid Voice",
    219: "Phoenix Down",
    220: "Innards Out",
    221: "Dazzling",
    222: "Gluttony",
    223: "Mountaineer",
    224: "Friend Guard",
    225: "Harvest",
    226: "Telepathy",
    227: "Illusion",
    228: "Cheek Pouch",
    229: "Protosynthesis",
    230: "Stakeout",
    231: "Comatose",
    232: "Dancer",
    233: "Bone Zone",
    234: "Receiver",
    235: "Self Sufficient",
    236: "Neuroforce",
    237: "Galvanize",
    238: "Intrepid Sword",
    239: "Valiant Shield",
    240: "Striker",
    241: "Cotton Down",
    242: "Sword Of Ruin",
    243: "Sharpness",
    244: "Vessel Of Ruin",
    245: "Steam Engine",
    246: "Punk Rock",
    247: "Sand Spit",
    248: "Ice Scales",
    249: "Ripen",
    250: "Ice Face",
    251: "Rocky Payload",
    252: "Blazing Soul",
    253: "Screen Cleaner",
    254: "Well-Baked Body",
    255: "Sage Power",
}

# Complete vanilla ability names (Gen III through Gen V, IDs 1-165).
# For non-RR/CFRU games. RR reassigns many IDs 72+ to different abilities.
# Sources: pret/pokefirered, pret/pokeplatinum, pret/pokeblack2
_VANILLA_ABILITY_NAMES: dict[int, str] = {
    1: "Stench", 2: "Drizzle", 3: "Speed Boost", 4: "Battle Armor",
    5: "Sturdy", 6: "Damp", 7: "Limber", 8: "Sand Veil",
    9: "Static", 10: "Volt Absorb", 11: "Water Absorb", 12: "Oblivious",
    13: "Cloud Nine", 14: "Compound Eyes", 15: "Insomnia", 16: "Color Change",
    17: "Immunity", 18: "Flash Fire", 19: "Shield Dust", 20: "Own Tempo",
    21: "Suction Cups", 22: "Intimidate", 23: "Shadow Tag", 24: "Rough Skin",
    25: "Wonder Guard", 26: "Levitate", 27: "Effect Spore", 28: "Synchronize",
    29: "Clear Body", 30: "Natural Cure", 31: "Lightning Rod", 32: "Serene Grace",
    33: "Swift Swim", 34: "Chlorophyll", 35: "Illuminate", 36: "Trace",
    37: "Huge Power", 38: "Poison Point", 39: "Inner Focus", 40: "Magma Armor",
    41: "Water Veil", 42: "Magnet Pull", 43: "Soundproof", 44: "Rain Dish",
    45: "Sand Stream", 46: "Pressure", 47: "Thick Fat", 48: "Early Bird",
    49: "Flame Body", 50: "Run Away", 51: "Keen Eye", 52: "Hyper Cutter",
    53: "Pickup", 54: "Truant", 55: "Hustle", 56: "Cute Charm",
    57: "Plus", 58: "Minus", 59: "Forecast", 60: "Sticky Hold",
    61: "Shed Skin", 62: "Guts", 63: "Marvel Scale", 64: "Liquid Ooze",
    65: "Overgrow", 66: "Blaze", 67: "Torrent", 68: "Swarm",
    69: "Rock Head", 70: "Drought", 71: "Arena Trap", 72: "Vital Spirit",
    73: "White Smoke", 74: "Pure Power", 75: "Shell Armor", 76: "Cacophony",
    77: "Air Lock",
    # Gen IV (78-124)
    78: "Tangled Feet", 79: "Motor Drive", 80: "Rivalry", 81: "Steadfast",
    82: "Snow Cloak", 83: "Gluttony", 84: "Anger Point", 85: "Unburden",
    86: "Heatproof", 87: "Simple", 88: "Dry Skin", 89: "Download",
    90: "Iron Fist", 91: "Poison Heal", 92: "Adaptability", 93: "Skill Link",
    94: "Hydration", 95: "Solar Power", 96: "Quick Feet", 97: "Normalize",
    98: "Sniper", 99: "Magic Guard", 100: "No Guard", 101: "Stall",
    102: "Technician", 103: "Leaf Guard", 104: "Klutz", 105: "Mold Breaker",
    106: "Super Luck", 107: "Aftermath", 108: "Anticipation", 109: "Forewarn",
    110: "Unaware", 111: "Tinted Lens", 112: "Filter", 113: "Slow Start",
    114: "Scrappy", 115: "Storm Drain", 116: "Ice Body", 117: "Solid Rock",
    118: "Snow Warning", 119: "Honey Gather", 120: "Frisk", 121: "Reckless",
    122: "Multitype", 123: "Flower Gift", 124: "Bad Dreams",
    # Gen V (125-164)
    125: "Pickpocket", 126: "Sheer Force", 127: "Contrary", 128: "Unnerve",
    129: "Defiant", 130: "Defeatist", 131: "Cursed Body", 132: "Healer",
    133: "Friend Guard", 134: "Weak Armor", 135: "Heavy Metal", 136: "Light Metal",
    137: "Multiscale", 138: "Toxic Boost", 139: "Flare Boost", 140: "Harvest",
    141: "Telepathy", 142: "Moody", 143: "Overcoat", 144: "Poison Touch",
    145: "Regenerator", 146: "Big Pecks", 147: "Sand Rush", 148: "Wonder Skin",
    149: "Analytic", 150: "Illusion", 151: "Imposter", 152: "Infiltrator",
    153: "Mummy", 154: "Moxie", 155: "Justified", 156: "Rattled",
    157: "Magic Bounce", 158: "Sap Sipper", 159: "Prankster", 160: "Sand Force",
    161: "Iron Barbs", 162: "Zen Mode", 163: "Victory Star", 164: "Turboblaze",
    165: "Teravolt",
}


# RR per-species ability name overrides.
#
# CFRU uses a single ability ID for several functionally-identical ability pairs
# (e.g. ABILITY_GOOEY is the engine name; the dex displays "Tangling Hair" for
# Diglett-Alola). RR's GetAbilityNameOverride() function provides the per-species
# display.
#
# Most entries are auto-generated from funnotbun's duplicate_abilities.h — the
# same source the funnotbun web dex uses. Regenerate with:
#   python tools/gen_ability_name_overrides_rr.py
#
# Manual entries below cover renames that funnotbun's file doesn't include
# (observed in-game but absent from upstream data). Manual entries take
# precedence over generated ones (use sparingly, prefer fixing the generator
# or filing a funnotbun PR).
#
# Keys are (ability_id, natdex_base_form). Alolan/regional forms map to their
# base form NatDex via to_national() in the lookup path.
from server.rr_ability_overrides import CFRU_ABILITY_NAME_OVERRIDES_GENERATED  # noqa: E402

CFRU_ABILITY_NAME_OVERRIDES_MANUAL: dict[tuple[int, int], str] = {
    # Carried over from the pre-generator hand-curated dict. funnotbun's
    # duplicate_abilities.h does not include these as of 2026-05-17. If a future
    # funnotbun update adds them, the generator will pick them up and these can
    # be removed.

    # ABILITY_STURDY (5) / Nine Lives
    (5, 52): "Nine Lives", (5, 53): "Nine Lives",                  # Meowth (incl. Alolan), Persian (incl. Alolan)
    # ABILITY_STURDY (5) / Focus Belt
    (5, 67): "Focus Belt", (5, 68): "Focus Belt",                  # Machoke, Machamp
    # ABILITY_MULTISCALE (81) / Multieye
    (81, 343): "Multieye", (81, 344): "Multieye",                  # Baltoy, Claydol
    # ABILITY_FILTER (101) / Solid Rock
    (101, 323): "Solid Rock", (101, 464): "Solid Rock",
    (101, 564): "Solid Rock", (101, 565): "Solid Rock", (101, 825): "Solid Rock",
    # ABILITY_QUICKFEET (112) / Strong Jaws
    (112, 262): "Strong Jaws",                                     # Mightyena
    # ABILITY_MOXIE (118) / Fiery Neigh
    (118, 77): "Fiery Neigh", (118, 78): "Fiery Neigh",            # Ponyta-Galar, Rapidash-Galar
    # ABILITY_MOXIE (118) / Pride
    (118, 519): "Pride", (118, 520): "Pride", (118, 521): "Pride", # Pidove, Tranquill, Unfezant
    # ABILITY_MOXIE (118) / Shocking Neigh
    (118, 522): "Shocking Neigh", (118, 523): "Shocking Neigh",    # Blitzle, Zebstrika
    # ABILITY_MOXIE (118) / Chilling Neigh
    (118, 896): "Chilling Neigh",                                  # Spectrier
    # ABILITY_GOOEY (121) / Tangling Wool
    (121, 179): "Tangling Wool", (121, 180): "Tangling Wool", (121, 181): "Tangling Wool",  # Mareep, Flaaffy, Ampharos
    # ABILITY_GRIMNEIGH (123) / Royal Roar
    (123, 667): "Royal Roar", (123, 668): "Royal Roar",            # Litleo, Pyroar
    # ABILITY_PROTEAN (150) / Subterfuge
    (150, 414): "Subterfuge",                                      # Mothim
    # ABILITY_PARENTALBOND (151) / Brain Bond
    (151, 203): "Brain Bond",                                      # Girafarig
    # ABILITY_GORILLATACTICS (215) / Crabby Tactics
    (215, 739): "Crabby Tactics",                                  # Crabrawler/Crabominable
    # ABILITY_DAZZLING (221) / Queenly Majesty
    (221, 31): "Queenly Majesty",                                  # Nidoqueen
    # ABILITY_DAUNTLESSSHIELD (239) / Face Shield
    (239, 410): "Face Shield", (239, 411): "Face Shield",          # Shieldon, Bastiodon
    # ABILITY_DAUNTLESSSHIELD (239) / Honey Armor
    (239, 416): "Honey Armor",                                     # Vespiquen
    # ABILITY_COTTONDOWN (241) / Cotton Cloud
    (241, 333): "Cotton Cloud", (241, 334): "Cotton Cloud",        # Swablu, Altaria
    # ABILITY_STALWART (244) / Propeller Tail
    (244, 846): "Propeller Tail", (244, 847): "Propeller Tail",    # Arrokuda, Barraskewda
    # ABILITY_PUNKROCK (246) / Bellow
    (246, 293): "Bellow", (246, 294): "Bellow", (246, 295): "Bellow",  # Whismur, Loudred, Exploud
    # ABILITY_PUNKROCK (246) / Sound Waves
    (246, 329): "Sound Waves", (246, 330): "Sound Waves",          # Vibrava, Flygon
    (246, 714): "Sound Waves", (246, 715): "Sound Waves",          # Noibat, Noivern
    # ABILITY_ICESCALES (248) / Icy Skin
    (248, 124): "Icy Skin", (248, 238): "Icy Skin", (248, 471): "Icy Skin",  # Jynx, Smoochum, Glaceon
    # ABILITY_ICESCALES (248) / Dusty Scales
    (248, 269): "Dusty Scales",                                    # Dustox
}

CFRU_ABILITY_NAME_OVERRIDES: dict[tuple[int, int], str] = {
    **CFRU_ABILITY_NAME_OVERRIDES_GENERATED,
    **CFRU_ABILITY_NAME_OVERRIDES_MANUAL,
}


def ability_name(ability_id: int, is_rr: bool = False, species_id: int = 0) -> str:
    """Return display name for an ability ID, respecting profile differences.

    species_id: CFRU internal species ID (optional). When provided for RR/CFRU
    runs, enables species-specific name overrides for aliased abilities
    (e.g. ABILITY_GOOEY shows "Tangling Hair" for Alolan Diglett).
    """
    if not ability_id:
        return ""
    if is_rr and species_id:
        natdex = to_national(species_id)
        override = CFRU_ABILITY_NAME_OVERRIDES.get((ability_id, natdex))
        if override:
            return override
    if not is_rr:
        return _VANILLA_ABILITY_NAMES.get(ability_id, f"#{ability_id}")
    return RR_ABILITY_NAMES.get(ability_id, ABILITY_NAMES.get(ability_id, f"#{ability_id}"))


# Ability descriptions for tooltip display.
# RR-specific descriptions from funnotbun/funnotbun.github.io (Radical Red Dex).
# Vanilla fallbacks from pret/pokefirered for abilities not overridden by RR.
ABILITY_DESCRIPTIONS: dict[int, str] = {
    1: "Helps repel wild Pokémon.",
    2: "Summons Rain for 5 turns.",
    3: "Gradually boosts Speed.",
    4: "Blocks critical hits.",
    5: "Negates 1-hit KO attacks.",
    6: "Prevents self-destruction.",
    7: "Prevents paralysis.",
    8: "Ups evasion in a sandstorm.",
    9: "Paralyzes on contact.",
    10: "Turns electricity into HP.",
    11: "Changes water into HP.",
    12: "Prevents attraction and Intimidate.",
    13: "Negates weather effects.",
    14: "Raises accuracy by 30%.",
    15: "Prevents sleep.",
    16: "Immune to Wind moves and gets +1 Attack instead.",
    17: "Prevents poisoning.",
    18: "Powers up if hit by fire.",
    19: "Stops added effects and entry hazard damage.",
    20: "Prevents confusion and Intimidate.",
    21: "Firmly anchors the body.",
    22: "Lowers the foe's Attack.",
    23: "Prevents the foe's escape.",
    24: "Has a 30% chance to move first.",
    25: "\u201cSuper effective\u201d hits only.",
    26: "Not hit by Ground attacks.",
    27: "Leaves spores on contact.",
    28: "Passes on status problems.",
    29: "Prevents ability reduction.",
    30: "Heals upon switching out.",
    31: "Draws electrical moves.",
    32: "Promotes added effects.",
    33: "Doubles Speed in Rain.",
    34: "Doubles Speed in Sun.",
    35: "Spits prey if damaged after a swim.",
    36: "Copies special ability.",
    37: "Doubles Attack stat.",
    38: "Powers up Dragon-type moves by 50%.",
    39: "Prevents flinching and Intimidate.",
    40: "Prevents frostbite.",
    41: "Prevents burns.",
    42: "Traps Steel-type Pokémon.",
    43: "Avoids sound-based moves.",
    44: "Slight HP recovery in rain.",
    45: "Summons Sand for 5 turns.",
    46: "Raises foe's PP usage.",
    47: "Ups resistance to Fire and Ice moves by 50%.",
    48: "Awakens quickly from sleep.",
    49: "Burns the foe on contact.",
    50: "Makes escaping easier.",
    51: "Stops self-lowering stats and prevents recoil.",
    52: "Prevents Attack reduction.",
    53: "10% chance to pickup items after battle.",
    54: "Moves only every two turns.",
    55: "Reduces accuracy by 20% but boosts Atk by 50%.",
    56: "Has a 30% chance to infatuate on contact.",
    57: "Boosts Sp. Atk by 50% with Minus.",
    58: "Boosts Sp. Atk by 50% with Plus.",
    59: "Changes with the weather.",
    60: "Prevents item theft.",
    61: "Has a 30% chance to heal its own status problem.",
    62: "Boosts Atk by 50% if statused.",
    63: "Boosts Defense by 50% if statused.",
    64: "Draining causes injury.",
    65: "Boosts Grass moves by 50% at 1/3 or less HP.",
    66: "Boosts Fire moves by 50% at 1/3 or less HP.",
    67: "Boosts Water moves by 50% at 1/3 or less HP.",
    68: "Boosts Bug moves by 50% at 1/3 or less HP.",
    69: "Prevents recoil damage.",
    70: "Summons Sun for 5 turns.",
    71: "Prevents fleeing.",
    72: "Resists Ghost and immune to status.",
    73: "Both Unnerve and Grim Neigh.",
    74: "All Abilities are nullified.",
    75: "Super effective moves can't miss & boosted by 20%.",
    76: "Changes form each turn.",
    77: "Both Unnerve and Moxie.",
    78: "Keeps user and allies awake.",
    79: "Multi-hit moves hit five times.",
    80: "Raises Speed if hit by an Electric-type move.",
    81: "Reduces damage at full HP.",
    82: "Boosts moves with less than 60 BP by 1.5x.",
    83: "Can hit Ghost-types and prevents Intimidate.",
    84: "Critical hits land more often.",
    85: "Critical hits deal 2.25x instead of 1.5x.",
    86: "Heals 1/3 of HP when switching out.",
    87: "Gives +1 priority to status moves.",
    88: "Boosts same type attacks by 2x instead of 1.5x.",
    89: "Raises accuracy by 20%.",
    90: "Reflects status moves.",
    91: "Boosts moves with recoil by 20%.",
    92: "Removes added effects to boost damage by 30%.",
    93: "Boosts punching moves by 30%.",
    94: "Boosts moves by 1.25x if foe is of same gender.",
    95: "Boosts Rock, Ground, and Steel moves in sand.",
    96: "Boosts Sp. Atk by 50% in Sun.",
    97: "Weakens the power of Fire-type moves.",
    98: "Reduces HP if it is hot. Water restores HP.",
    99: "Doubles the damage of not very effective moves.",
    100: "Weakens super effective moves by 25%.",
    101: "Moves ignore redirection.",
    102: "Passes through the foe's barriers.",
    103: "Adjusts power based on the foe's stats.",
    104: "Restores 1/8 of maximum HP if poisoned.",
    105: "HP heals in hail, can freeze on hit.",
    106: "Boosts power by 20% & Speed by 50% in first turn.",
    107: "Heals status problems in rain.",
    108: "Boosts power depending on fainted allies.",
    109: "+Atk/SpA/Spe, -Def/SpD if hit lowers HP below 50%.",
    110: "Full immunity to status moves.",
    111: "Summons Snow for 5 turns.",
    112: "Doubles Speed if it has a status condition.",
    113: "Boosts Attack if hit by a Grass-type move.",
    114: "Immune to weather and powder.",
    115: "Only damaged by attacks.",
    116: "Immune to some ball and bomb moves.",
    117: "Gives +1 priority to Flying moves at full HP.",
    118: "Boosts Attack after knocking out a Pokémon.",
    119: "Fainting damages the foe by 1/4 its max HP.",
    120: "30% chance to disable a foe's move when hit.",
    121: "Lowers the foe's Speed if hit by a contact move.",
    122: "Contact with this Pokémon spreads this Ability.",
    123: "Inflicts damage (1/8 HP) to a foe on contact.",
    124: "Doubles Speed in Sand.",
    125: "Moving last boosts power by 30%.",
    126: "Ensures that all moves land.",
    127: "Boosts aura and pulse moves by 50%.",
    128: "Boosts moves that make contact by 30%.",
    129: "Boosts biting moves by 50%.",
    130: "Raises own and ally's accuracy by 10%.",
    131: "Immune to & draws in Water moves to boost Sp. Atk.",
    132: "Powers up all Dark-type moves by 33%.",
    133: "Powers up all Fairy-type moves by 33%.",
    134: "Summons Grassy Terrain for 5 turns when hit.",
    135: "Normal moves become Ice & boosted by 20%.",
    136: "Normal moves become Fairy & boosted by 20%.",
    137: "Normal moves become Flying & boosted by 20%.",
    138: "Doubles Special Attack stat.",
    139: "Doubles Speed if item is lost.",
    140: "Doubles stat changes.",
    141: "Ignores foe's stat changes.",
    142: "Attack rises when its stats are lowered.",
    143: "Raises Sp. Atk when its stats are lowered.",
    144: "Halves Atk/Sp.Atk when its HP is at 1/3 or less.",
    145: "Takes a while to get going.",
    146: "Boosts Attack by 50% when poisoned.",
    147: "Boosts Sp. Atk by 50% when burned.",
    148: "Halves physical damage.",
    149: "Ups status move evasion.",
    150: "Changes type to match move.",
    151: "Moves hit a second time for 25% damage.",
    152: "Moves can be used regardless of abilities.",
    153: "Set E.Terrain for 5 turns. Up Sp.Atk by 30% in it.",
    154: "Sets Sun for 5 turns. Boosts Atk by 30% in Sun.",
    155: "Transforms into Zen Mode.",
    156: "Transforms into Ash Greninja after it KOs a foe.",
    157: "KOing a foe boosts its highest stat.",
    158: "Poison can hit Steel types.",
    159: "Takes one hit with no damage.",
    160: "Switches out below half HP.",
    161: "Halves contact hits. Takes 2x to Fire moves.",
    162: "Boosts own and ally Steel moves by 50%.",
    163: "Gives a perish count when hit by a contact move.",
    164: "Trades Abilities on contact.",
    165: "Transforms to Complete form when below half HP.",
    166: "Lowers the Attack of all other Pokémon by 25%.",
    167: "Punch moves hit a second time for 50% dmg.",
    168: "Schools up when above 25% HP.",
    169: "Lowers the Sp.Def of all other Pokémon by 25%.",
    170: "Lowers shields below half HP.",
    171: "Doubles Speed in Hail.",
    172: "Boosts Sp. Atk every time a Pokémon faints.",
    173: "Boosts Defense when hit by an attack.",
    174: "Changes form upon switching.",
    175: "Fire-type hits raise Attack. Immune to burn.",
    176: "Gives +3 priority to healing moves.",
    177: "Doubles Water moves. Takes half from Fire moves.",
    178: "Halves Water damage & raises Defense.",
    179: "Attacks that poison also drain.",
    180: "Changes type to match held Plate.",
    181: "Sets Electric Terrain for 5 turns.",
    182: "Creates Grassy Terrain for 5 turns.",
    183: "Creates Misty Terrain for 5 turns.",
    184: "Creates a Psychic Terrain for 5 turns.",
    185: "Doubles Speed in Electric Terrain.",
    186: "Ups Defense by 50% in Grassy Terrain.",
    187: "Maxes Attack after a critical hit.",
    188: "Absorbs Ground moves.",
    189: "Warns of the foe's strongest move.",
    190: "Reveals the foe's held item.",
    191: "Reverses stat changes.",
    192: "Stops the foe from eating Berries.",
    193: "Hits by physical moves drop Def but raises Speed.",
    194: "Doubles weight.",
    195: "Boosts highest stat in Electric Terrain.",
    196: "Being flinched raises Speed.",
    197: "Transforms into foe upon entering battle.",
    198: "Dark-type hits raise Attack.",
    199: "Contact moves bypass Protect.",
    200: "Guarantees critical-hits on poisoned foes.",
    201: "Protects against mental problems.",
    202: "Sets Toxic Spikes when hit by a physical move.",
    203: "Protects from status in Sun.",
    204: "Gains Charge when hit by an attack.",
    205: "Boosts Atk and Speed by 50% in Sun.",
    206: "Reduces sleeping foe's HP by 1/8 at end of turn.",
    207: "Boosts Sp. Atk after knocking out a Pokémon.",
    208: "Powers up Electric-type moves by 50%.",
    209: "30% chance to poison foe on contact.",
    210: "Can steal foe's item after attack.",
    211: "Switches form when attacking.",
    212: "Summons Heavy Rain that nullifies Fire moves.",
    213: "Summons Harsh Sun that nullifies Water moves.",
    214: "Changes weather to remove Flying-type weaknesses.",
    215: "Ups Atk by 50% but only uses 1st picked move.",
    216: "Raises Sp. Atk if hit sends it below half HP.",
    217: "Decreases super effective moves by 50%.",
    218: "Sound moves become Water-type & boosted by 20%.",
    219: "Revives to half health on first faint.",
    220: "Fainting damages the foe equal to its last HP.",
    221: "Blocks priority moves.",
    222: "Eats Berries at 50% HP instead of 25%.",
    223: "Immune to Rock attacks and Stealth Rocks.",
    224: "Reduces damage done to allies.",
    225: "50% chance to restore berry.",
    226: "Protects from allies' moves.",
    227: "Enters disguised and deals 30% more damage.",
    228: "Eating Berries restores HP by 1/3.",
    229: "Boosts highest stat in Sun.",
    230: "Doubles damage if foe switches out.",
    231: "Acts as if asleep.",
    232: "Copies dance moves.",
    233: "Bone moves ignore immunity and resistances.",
    234: "Takes over a KOed ally's Ability.",
    235: "Recover 1/16 HP at end of turn.",
    236: "Powers up super effective hits by 25%.",
    237: "Normal moves become Electric & boosted by 20%.",
    238: "Raises Attack on entry.",
    239: "Raises Defense on entry.",
    240: "Boosts kicking moves by 30%.",
    241: "Lowers everyone's Speed when hit.",
    242: "Lowers the Defense of all other Pokémon by 25%.",
    243: "Boosts slashing moves by 50%.",
    244: "Lowers the Sp.Atk of all other Pokémon by 25%.",
    245: "Being hit by Fire/Water moves maxes Speed.",
    246: "Boosts sound moves by 30% & resists them by 50%.",
    247: "Summons Sand for 5 turns when hit.",
    248: "Halves Special damage.",
    249: "Doubles Berry effects.",
    250: "Takes one free physical hit and changes form.",
    251: "Boosts power of Rock moves by 50%.",
    252: "Gives +1 priority to Fire moves at full HP.",
    253: "Screens are removed on entry.",
    254: "Immune to Fire moves and gets +2 Defense instead.",
    255: "Ups SpAtk by 50% but only uses 1st picked move.",
}

# Complete vanilla ability descriptions (Gen III through Gen V, IDs 1-165).
# For non-RR/CFRU games. From pret/pokefirered, pret/pokeplatinum, pret/pokeblack2.
_VANILLA_ABILITY_DESCRIPTIONS: dict[int, str] = {
    1: "Helps repel wild Pokémon.",
    2: "Summons rain in battle.",
    3: "Gradually boosts Speed.",
    4: "Blocks critical hits.",
    5: "Negates one-hit KO attacks.",
    6: "Prevents self-destruction.",
    7: "Prevents paralysis.",
    8: "Boosts evasion in a sandstorm.",
    9: "Contact may cause paralysis.",
    10: "Restores HP if hit by Electric.",
    11: "Restores HP if hit by Water.",
    12: "Prevents attraction.",
    13: "Negates weather effects.",
    14: "Boosts move accuracy.",
    15: "Prevents sleep.",
    16: "Changes type to foe's move type.",
    17: "Prevents poisoning.",
    18: "Powers up if hit by Fire.",
    19: "Blocks secondary move effects.",
    20: "Prevents confusion.",
    21: "Negates forced switching.",
    22: "Lowers the foe's Attack.",
    23: "Prevents the foe from escaping.",
    24: "Hurts the foe on contact.",
    25: "Only super-effective hits land.",
    26: "Not hit by Ground-type moves.",
    27: "Contact may poison, paralyze, or sleep.",
    28: "Passes on status problems.",
    29: "Prevents stat reduction.",
    30: "Heals status on switching out.",
    31: "Draws in Electric-type moves.",
    32: "Doubles secondary effect chance.",
    33: "Boosts Speed in rain.",
    34: "Boosts Speed in sunshine.",
    35: "Raises wild encounter rate.",
    36: "Copies the foe's ability.",
    37: "Doubles Attack stat.",
    38: "Contact may poison.",
    39: "Prevents flinching.",
    40: "Prevents freezing.",
    41: "Prevents burns.",
    42: "Traps Steel-type Pokémon.",
    43: "Blocks sound-based moves.",
    44: "Gradual HP recovery in rain.",
    45: "Summons a sandstorm in battle.",
    46: "The foe uses double PP.",
    47: "Resists Fire and Ice moves.",
    48: "Awakens from sleep quickly.",
    49: "Contact may burn the foe.",
    50: "Ensures fleeing from wild battles.",
    51: "Prevents accuracy reduction.",
    52: "Prevents Attack reduction.",
    53: "May pick up items after battle.",
    54: "Can only attack every other turn.",
    55: "Boosts Attack but lowers accuracy.",
    56: "Contact may cause attraction.",
    57: "Boosts Sp.Atk with Minus.",
    58: "Boosts Sp.Atk with Plus.",
    59: "Changes form with weather.",
    60: "Prevents item theft.",
    61: "May heal own status problems.",
    62: "Boosts Attack if statused.",
    63: "Boosts Defense if statused.",
    64: "Damages foes using draining moves.",
    65: "Powers up Grass moves in a pinch.",
    66: "Powers up Fire moves in a pinch.",
    67: "Powers up Water moves in a pinch.",
    68: "Powers up Bug moves in a pinch.",
    69: "Prevents recoil damage.",
    70: "Summons sunshine in battle.",
    71: "Prevents foes from fleeing.",
    72: "Prevents sleep.",
    73: "Prevents stat reduction.",
    74: "Doubles Attack stat.",
    75: "Blocks critical hits.",
    76: "Blocks sound-based moves.",
    77: "Negates all weather effects.",
    # Gen IV (78-124)
    78: "Boosts evasion if confused.",
    79: "Boosts Speed if hit by Electric.",
    80: "Deals more damage to same gender.",
    81: "Boosts Speed when flinched.",
    82: "Boosts evasion in hail.",
    83: "Eats held berries at half HP.",
    84: "Maxes Attack after a critical hit.",
    85: "Doubles Speed when item is used or lost.",
    86: "Halves damage from Fire-type moves.",
    87: "Doubles all stat changes.",
    88: "Absorbs Water; weakened by Fire.",
    89: "Adjusts Attack or Sp.Atk on entry.",
    90: "Boosts punching moves by 20%.",
    91: "Restores HP when poisoned.",
    92: "Same-type moves do more damage.",
    93: "Multi-hit moves always hit 5 times.",
    94: "Heals status problems in rain.",
    95: "Boosts Sp.Atk in sun; loses HP.",
    96: "Boosts Speed when statused.",
    97: "All moves become Normal type.",
    98: "Boosts critical-hit damage.",
    99: "Only harmed by direct attacks.",
    100: "All moves hit without fail.",
    101: "Always moves last in its priority.",
    102: "Boosts weaker moves (60 BP or less).",
    103: "Prevents status in sunshine.",
    104: "Cannot use held items.",
    105: "Moves ignore the target's ability.",
    106: "Heightened critical-hit ratio.",
    107: "Damages attacker upon fainting.",
    108: "Senses dangerous foe moves.",
    109: "Reveals the foe's strongest move.",
    110: "Ignores the foe's stat changes.",
    111: "Powers up not-very-effective moves.",
    112: "Reduces super-effective damage.",
    113: "Halves Attack and Speed for 5 turns.",
    114: "Hits Ghost types with Normal and Fighting.",
    115: "Draws in Water moves; boosts Sp.Atk.",
    116: "Gradual HP recovery in hail.",
    117: "Reduces super-effective damage.",
    118: "Summons hail in battle.",
    119: "May gather Honey after battle.",
    120: "Reveals the foe's held item.",
    121: "Boosts recoil moves.",
    122: "Changes type to match held Plate.",
    123: "Boosts allies' Attack and Sp.Def in sun.",
    124: "Damages sleeping foes each turn.",
    # Gen V (125-165)
    125: "Steals the foe's item on contact.",
    126: "Removes secondary effects; boosts power.",
    127: "Inverts stat changes.",
    128: "Prevents foes from eating berries.",
    129: "Boosts Attack when stats are lowered.",
    130: "Halves Attack and Sp.Atk at half HP.",
    131: "May disable the foe's move on contact.",
    132: "May heal an ally's status problem.",
    133: "Reduces damage to allies.",
    134: "Physical hits boost Speed; lower Defense.",
    135: "Doubles the Pokémon's weight.",
    136: "Halves the Pokémon's weight.",
    137: "Reduces damage taken at full HP.",
    138: "Boosts Attack when poisoned.",
    139: "Boosts Sp.Atk when burned.",
    140: "May re-harvest a used Berry.",
    141: "Avoids damage from allies.",
    142: "Randomly raises and lowers a stat.",
    143: "Blocks weather damage and powder moves.",
    144: "Moves may poison the foe on contact.",
    145: "Restores HP on switching out.",
    146: "Prevents Defense reduction.",
    147: "Doubles Speed in a sandstorm.",
    148: "Makes status moves more likely to miss.",
    149: "Boosts move power if moving last.",
    150: "Enters battle disguised as another.",
    151: "Transforms into the opposing Pokémon.",
    152: "Bypasses Reflect, Light Screen, etc.",
    153: "Spreads Mummy ability on contact.",
    154: "Boosts Attack after knocking out a foe.",
    155: "Boosts Attack when hit by Dark-type.",
    156: "Boosts Speed when hit by Bug/Dark/Ghost.",
    157: "Reflects status moves back.",
    158: "Absorbs Grass moves; boosts Attack.",
    159: "Gives priority to status moves.",
    160: "Boosts Rock/Ground/Steel in sandstorm.",
    161: "Damages the foe on contact.",
    162: "Changes form when HP drops below half.",
    163: "Boosts allies' accuracy.",
    164: "Moves ignore the target's ability.",
    165: "Moves ignore the target's ability.",
}


def ability_description(ability_id: int, is_rr: bool = False) -> str:
    """Return tooltip description for an ability ID."""
    if not ability_id:
        return ""
    if not is_rr:
        return _VANILLA_ABILITY_DESCRIPTIONS.get(ability_id, "")
    return ABILITY_DESCRIPTIONS.get(ability_id, "")


def base_form(species_id: int, is_rr: bool = True) -> int:
    """Return the base-form species ID for any mon in an evolution chain.

    Single-stage mons and base forms return themselves.
    species_id is ALWAYS a CFRU internal ID (all ROM types use this).
    RR uses its own ROM evolution graph, including distinct regional chains.
    Other titles keep the generic CFRU table used by their existing adapters.
    """
    families = _RR_EVO_FAMILY if is_rr else EVO_FAMILY
    return families.get(species_id, species_id)


def natdex_base_form(natdex_id: int) -> int:
    """Return the base-form NatDex ID for a Gen 4/5+ species.

    Gen 4/5 adapters receive NatDex IDs directly. EVO_FAMILY is keyed by
    CFRU internal IDs, so a round-trip conversion is required.
    """
    cfru_id = to_cfru(natdex_id)
    base_cfru = EVO_FAMILY.get(cfru_id, cfru_id)
    return to_national(base_cfru)


# -- CFRU alt-form -> PokeAPI sprite ID --
# Maps CFRU internal form IDs to PokeAPI pokemon IDs for sprite URLs.
# Generated by tools/gen_form_data.py -- do not edit manually.
CFRU_FORM_SPRITE_ID: dict[int, int] = {
    603:550,    707:10004,    708:10005,    709:10004,    710:10005,    713:10008,    714:10009,    715:10010,    716:10011,    717:10012,    718:10007,    719:10006,    736:10016,    737:10017,    752:10022,    753:10023,    754:10019,    755:10020,    756:10021,    757:10024,    832:10025,    833:10026,    869:10033,    870:10034,    871:10035,    872:10036,    873:10090,    874:10073,    875:10037,    876:10071,    877:10038,    878:10039,    879:10040,    880:10041,    881:10042,    882:10043,    883:10044,    884:10045,    885:10072,    886:10046,    887:10047,    888:10048,    889:10049,    890:10065,    891:10050,    892:10064,    893:10051,    894:10066,    895:10052,    896:10053,    897:10054,    898:10055,    899:10070,    900:10087,    901:10067,    902:10056,    903:10057,    904:10074,    905:10089,    906:10076,    907:10062,    908:10063,    909:10078,    910:10077,    911:10079,    912:10088,    913:10058,    914:10059,    915:10060,    916:10068,    917:10069,    918:10075,    919:10245,    920:10246,    991:774,    1020:10091,    1021:10092,    1022:10100,    1023:10101,    1024:10102,    1025:10103,    1026:10104,    1027:10105,    1028:10106,    1029:10107,    1030:10108,    1031:10109,    1032:10110,    1033:10111,    1034:10112,    1035:10113,    1037:10114,    1039:10115,    1040:10001,    1041:10002,    1042:10003,    1065:10136,    1066:10140,    1082:10152,    1089:10082,    1090:10080,    1184:892,    1203:10186,    1208:10191,    1210:10193,    1211:10194,    1212:10161,    1213:10162,    1214:10163,    1215:10164,    1216:10165,    1217:10166,    1219:10167,    1220:10168,    1221:10169,    1222:10170,    1223:10171,    1224:10172,    1225:10173,    1226:10174,    1227:10175,    1229:10176,    1233:10180,    1234:10229,    1235:10230,    1236:10231,    1238:10233,    1239:10234,    1240:10235,    1241:10236,    1242:10237,    1248:10242,    1249:10243,    1250:10244,    1259:10249,    1260:10195,    1261:10196,    1264:10199,    1265:10200,    1269:10204,    1271:10206,    1272:10207,    1274:10209,    1275:10210,    1276:10211,    1277:10212,    1278:10213,    1279:10214,    1280:10215,    1281:10216,    1282:10217,    1283:10218,    1285:10228,    1286:10220,    1287:10221,    1288:10222,    1289:10223,    1290:10224,    1291:10225,    1293:10227,
}

# -- CFRU alt-form types --
# Maps CFRU form IDs to (type1, type2) using Gen III type byte values.
# Generated by tools/gen_form_data.py -- do not edit manually.
CFRU_FORM_TYPES: dict[int, tuple[int, int]] = {
    603:(11,11),    707:(6,4),    708:(6,8),    709:(6,4),    710:(6,8),    713:(13,10),    714:(13,11),    715:(13,15),    716:(13,2),    717:(13,12),    718:(7,16),    719:(12,2),    736:(11,11),    737:(10,14),    752:(16,15),    753:(16,15),    754:(2,2),    755:(13,2),    756:(4,2),    757:(11,1),    832:(14,14),    833:(8,7),    869:(12,3),    870:(10,16),    871:(10,2),    872:(11,11),    873:(6,3),    874:(0,2),    875:(14,14),    876:(11,14),    877:(7,3),    878:(0,0),    879:(6,2),    880:(11,17),    881:(5,2),    882:(14,1),    883:(14,14),    884:(13,16),    885:(8,4),    886:(6,8),    887:(6,1),    888:(17,10),    889:(5,17),    890:(12,16),    891:(10,1),    892:(11,4),    893:(14,18),    894:(17,7),    895:(8,18),    896:(8,8),    897:(1,14),    898:(13,13),    899:(11,17),    900:(10,4),    901:(16,18),    902:(7,7),    903:(17,17),    904:(15,15),    905:(16,2),    906:(8,14),    907:(16,14),    908:(16,14),    909:(4,10),    910:(11,11),    911:(16,2),    912:(0,1),    913:(16,4),    914:(1,8),    915:(12,15),    916:(14,1),    917:(0,18),    918:(5,18),    919:(8,16),    920:(11,16),    991:(5,2),    1020:(17,0),    1021:(17,0),    1022:(13,14),    1023:(15,8),    1024:(15,8),    1025:(15,15),    1026:(15,18),    1027:(4,8),    1028:(4,8),    1029:(17,17),    1030:(17,17),    1031:(5,13),    1032:(5,13),    1033:(5,13),    1034:(3,17),    1035:(3,17),    1037:(12,16),    1039:(10,7),    1040:(14,14),    1041:(14,14),    1042:(14,14),    1065:(5,2),    1066:(5,2),    1082:(5,5),    1089:(13,13),    1090:(13,13),    1184:(1,17),    1203:(14,0),    1208:(1,11),    1210:(14,15),    1211:(14,7),    1212:(8,8),    1213:(14,14),    1214:(14,18),    1215:(14,14),    1216:(3,14),    1217:(1,1),    1219:(3,18),    1220:(15,14),    1221:(14,2),    1222:(1,2),    1223:(17,2),    1224:(3,14),    1225:(7,7),    1226:(17,0),    1227:(17,0),    1229:(15,15),    1233:(4,8),    1234:(10,5),    1235:(10,5),    1236:(13,12),    1238:(10,7),    1239:(17,3),    1240:(1,3),    1241:(11,17),    1242:(12,1),    1248:(8,16),    1249:(15,5),    1250:(12,1),    1259:(18,2),    1260:(12,3),    1261:(10,2),    1264:(13,13),    1265:(0,0),    1269:(11,15),    1271:(0,0),    1272:(3,3),    1274:(12,12),    1275:(10,10),    1276:(11,11),    1277:(2,8),    1278:(6,14),    1279:(11,5),    1280:(5,10),    1281:(12,16),    1282:(12,16),    1283:(4,4),    1285:(13,3),    1286:(10,6),    1287:(14,18),    1288:(17,18),    1289:(18,18),    1290:(8,8),    1291:(8,16),    1293:(1,11),
}


# ── Type data — Gen III type byte values and lookup ─────────────────────────

# Gen III type byte → display name (from pret/pokefirered include/constants/pokemon.h)
TYPE_NAMES: dict[int, str] = {
    0: "Normal", 1: "Fighting", 2: "Flying", 3: "Poison", 4: "Ground",
    5: "Rock", 6: "Bug", 7: "Ghost", 8: "Steel", 9: "???",
    10: "Fire", 11: "Water", 12: "Grass", 13: "Electric", 14: "Psychic",
    15: "Ice", 16: "Dragon", 17: "Dark", 18: "Fairy",
}

# (type1_byte, type2_byte) for each national dex species 1–386.
# Sourced from pret/pokefirered src/data/pokemon/base_stats/.
# Monotypes use the same byte for both slots.
_NATDEX_SPECIES_TYPES: dict[int, tuple[int, int]] = {
    # Gen I
    1:(12,3),2:(12,3),3:(12,3),4:(10,10),5:(10,10),6:(10,2),
    7:(11,11),8:(11,11),9:(11,11),10:(6,6),11:(6,6),12:(6,2),
    13:(6,3),14:(6,3),15:(6,3),16:(0,2),17:(0,2),18:(0,2),
    19:(0,0),20:(0,0),21:(0,2),22:(0,2),23:(3,3),24:(3,3),
    25:(13,13),26:(13,13),27:(4,4),28:(4,4),29:(3,3),30:(3,3),
    31:(3,4),32:(3,3),33:(3,3),34:(3,4),35:(0,0),36:(0,0),
    37:(10,10),38:(10,10),39:(0,0),40:(0,0),41:(3,2),42:(3,2),
    43:(12,3),44:(12,3),45:(12,3),46:(6,12),47:(6,12),48:(6,3),
    49:(6,3),50:(4,4),51:(4,4),52:(0,0),53:(0,0),54:(11,11),
    55:(11,11),56:(1,1),57:(1,1),58:(10,10),59:(10,10),60:(11,11),
    61:(11,11),62:(11,1),63:(14,14),64:(14,14),65:(14,14),66:(1,1),
    67:(1,1),68:(1,1),69:(12,3),70:(12,3),71:(12,3),72:(11,3),
    73:(11,3),74:(5,4),75:(5,4),76:(5,4),77:(10,10),78:(10,10),
    79:(11,14),80:(11,14),81:(13,8),82:(13,8),83:(0,2),84:(0,2),
    85:(0,2),86:(11,11),87:(11,15),88:(3,3),89:(3,3),90:(11,11),
    91:(11,15),92:(7,3),93:(7,3),94:(7,3),95:(5,4),96:(14,14),
    97:(14,14),98:(11,11),99:(11,11),100:(13,13),101:(13,13),
    102:(12,14),103:(12,14),104:(4,4),105:(4,4),106:(1,1),107:(1,1),
    108:(0,0),109:(3,3),110:(3,3),111:(4,5),112:(4,5),113:(0,0),
    114:(12,12),115:(0,0),116:(11,11),117:(11,11),118:(11,11),
    119:(11,11),120:(11,11),121:(11,14),122:(14,14),123:(6,2),
    124:(15,14),125:(13,13),126:(10,10),127:(6,6),128:(0,0),
    129:(11,11),130:(11,2),131:(11,15),132:(0,0),133:(0,0),
    134:(11,11),135:(13,13),136:(10,10),137:(0,0),138:(5,11),
    139:(5,11),140:(5,11),141:(5,11),142:(5,2),143:(0,0),
    144:(15,2),145:(13,2),146:(10,2),147:(16,16),148:(16,16),
    149:(16,2),150:(14,14),151:(14,14),
    # Gen II
    152:(12,12),153:(12,12),154:(12,12),155:(10,10),156:(10,10),
    157:(10,10),158:(11,11),159:(11,11),160:(11,11),161:(0,0),
    162:(0,0),163:(0,2),164:(0,2),165:(6,2),166:(6,2),167:(6,3),
    168:(6,3),169:(3,2),170:(11,13),171:(11,13),172:(13,13),
    173:(0,0),174:(0,0),175:(0,0),176:(0,2),177:(14,2),178:(14,2),
    179:(13,13),180:(13,13),181:(13,13),182:(12,12),183:(11,11),
    184:(11,11),185:(5,5),186:(11,11),187:(12,2),188:(12,2),
    189:(12,2),190:(0,0),191:(12,12),192:(12,12),193:(6,2),
    194:(11,4),195:(11,4),196:(14,14),197:(17,17),198:(17,2),
    199:(11,14),200:(7,7),201:(14,14),202:(14,14),203:(0,14),
    204:(6,6),205:(6,8),206:(0,0),207:(4,2),208:(8,4),209:(0,0),
    210:(0,0),211:(11,3),212:(6,8),213:(6,5),214:(6,1),215:(17,15),
    216:(0,0),217:(0,0),218:(10,10),219:(10,5),220:(15,4),221:(15,4),
    222:(11,5),223:(11,11),224:(11,11),225:(15,2),226:(11,2),
    227:(8,2),228:(17,10),229:(17,10),230:(11,16),231:(4,4),
    232:(4,4),233:(0,0),234:(0,0),235:(0,0),236:(1,1),237:(1,1),
    238:(15,14),239:(13,13),240:(10,10),241:(0,0),242:(0,0),
    243:(13,13),244:(10,10),245:(11,11),246:(5,4),247:(5,4),
    248:(5,17),249:(14,2),250:(10,2),251:(14,12),
    # Gen III
    252:(12,12),253:(12,12),254:(12,12),255:(10,10),256:(10,1),
    257:(10,1),258:(11,11),259:(11,4),260:(11,4),261:(17,17),
    262:(17,17),263:(0,0),264:(0,0),265:(6,6),266:(6,6),267:(6,2),
    268:(6,6),269:(6,3),270:(11,12),271:(11,12),272:(11,12),
    273:(12,12),274:(12,17),275:(12,17),276:(0,2),277:(0,2),
    278:(11,2),279:(11,2),280:(14,14),281:(14,14),282:(14,14),
    283:(6,11),284:(6,2),285:(12,12),286:(12,1),287:(0,0),
    288:(0,0),289:(0,0),290:(6,4),291:(6,2),292:(6,7),293:(0,0),
    294:(0,0),295:(0,0),296:(1,1),297:(1,1),298:(0,0),299:(5,5),
    300:(0,0),301:(0,0),302:(17,7),303:(8,8),304:(8,5),305:(8,5),
    306:(8,5),307:(1,14),308:(1,14),309:(13,13),310:(13,13),
    311:(13,13),312:(13,13),313:(6,6),314:(6,6),315:(12,3),
    316:(3,3),317:(3,3),318:(11,17),319:(11,17),320:(11,11),
    321:(11,11),322:(10,4),323:(10,4),324:(10,10),325:(14,14),
    326:(14,14),327:(0,0),328:(4,4),329:(4,16),330:(4,16),
    331:(12,12),332:(12,17),333:(0,2),334:(16,2),335:(0,0),
    336:(3,3),337:(5,14),338:(5,14),339:(11,4),340:(11,4),
    341:(11,11),342:(11,17),343:(4,14),344:(4,14),345:(5,12),
    346:(5,12),347:(5,6),348:(5,6),349:(11,11),350:(11,11),
    351:(0,0),352:(0,0),353:(7,7),354:(7,7),355:(7,7),356:(7,7),
    357:(12,2),358:(14,14),359:(17,17),360:(14,14),361:(15,15),
    362:(15,15),363:(15,11),364:(15,11),365:(15,11),366:(11,11),
    367:(11,11),368:(11,11),369:(11,5),370:(11,11),371:(16,16),
    372:(16,16),373:(16,2),374:(8,14),375:(8,14),376:(8,14),
    377:(5,5),378:(15,15),379:(8,8),380:(16,14),381:(16,14),
    382:(11,11),383:(4,4),384:(16,2),385:(8,14),386:(14,14),
    # Gen IV (387-493) — sourced from pret/pokeheartgold base_stats.
    # Note: Togekiss (468) is Normal/Flying in Gen 4-5; became Fairy/Flying in Gen 6+.
    # Wormadam (413) defaults to Plant cloak (Bug/Grass); form variants override.
    # Shaymin (492) defaults to Land form (Grass).
    387:(12,12),388:(12,12),389:(12,4),390:(10,10),391:(10,1),
    392:(10,1),393:(11,11),394:(11,11),395:(11,8),396:(0,2),
    397:(0,2),398:(0,2),399:(0,0),400:(0,11),401:(6,6),402:(6,6),
    403:(13,13),404:(13,13),405:(13,13),406:(12,3),407:(12,3),
    408:(5,5),409:(5,5),410:(5,8),411:(5,8),412:(6,6),413:(6,12),
    414:(6,2),415:(6,2),416:(6,2),417:(13,13),418:(11,11),
    419:(11,11),420:(12,12),421:(12,12),422:(11,11),423:(11,4),
    424:(0,0),425:(7,2),426:(7,2),427:(0,0),428:(0,0),429:(7,7),
    430:(17,2),431:(0,0),432:(0,0),433:(14,14),434:(3,17),435:(3,17),
    436:(8,14),437:(8,14),438:(5,5),439:(14,14),440:(0,0),441:(0,2),
    442:(7,17),443:(16,4),444:(16,4),445:(16,4),446:(0,0),447:(1,1),
    448:(1,8),449:(4,4),450:(4,4),451:(3,6),452:(3,17),453:(3,1),
    454:(3,1),455:(12,12),456:(11,11),457:(11,11),458:(11,2),
    459:(12,15),460:(12,15),461:(17,15),462:(13,8),463:(0,0),
    464:(4,5),465:(12,12),466:(13,13),467:(10,10),468:(0,2),
    469:(6,2),470:(12,12),471:(15,15),472:(4,2),473:(15,4),
    474:(0,0),475:(14,1),476:(5,8),477:(7,7),478:(15,7),479:(13,7),
    480:(14,14),481:(14,14),482:(14,14),483:(8,16),484:(11,16),
    485:(10,8),486:(0,0),487:(7,16),488:(14,14),489:(11,11),
    490:(11,11),491:(17,17),492:(12,12),493:(0,0),
    # Gen V (494-649) — sourced from pret/pokeblack base_stats.
    # Note: Cottonee (546) and Whimsicott (547) were pure Grass in Gen 5;
    # became Grass/Fairy in Gen 6+. Darmanitan (555) defaults to Standard form.
    494:(14,10),495:(12,12),496:(12,12),497:(12,12),498:(10,10),
    499:(10,1),500:(10,1),501:(11,11),502:(11,11),503:(11,11),
    504:(0,0),505:(0,0),506:(0,0),507:(0,0),508:(0,0),509:(17,17),
    510:(17,17),511:(12,12),512:(12,12),513:(10,10),514:(10,10),
    515:(11,11),516:(11,11),517:(14,14),518:(14,14),519:(0,2),
    520:(0,2),521:(0,2),522:(13,13),523:(13,13),524:(5,5),525:(5,5),
    526:(5,5),527:(14,2),528:(14,2),529:(4,4),530:(4,8),531:(0,0),
    532:(1,1),533:(1,1),534:(1,1),535:(11,11),536:(11,4),537:(11,4),
    538:(1,1),539:(1,1),540:(6,12),541:(6,12),542:(6,12),543:(6,3),
    544:(6,3),545:(6,3),546:(12,12),547:(12,12),548:(12,12),
    549:(12,12),550:(11,11),551:(4,17),552:(4,17),553:(4,17),
    554:(10,10),555:(10,10),556:(12,12),557:(6,5),558:(6,5),
    559:(17,1),560:(17,1),561:(14,2),562:(7,7),563:(7,7),
    564:(11,5),565:(11,5),566:(5,2),567:(5,2),568:(3,3),569:(3,3),
    570:(17,17),571:(17,17),572:(0,0),573:(0,0),574:(14,14),
    575:(14,14),576:(14,14),577:(14,14),578:(14,14),579:(14,14),
    580:(11,2),581:(11,2),582:(15,15),583:(15,15),584:(15,15),
    585:(0,12),586:(0,12),587:(13,2),588:(6,6),589:(6,8),590:(12,3),
    591:(12,3),592:(11,7),593:(11,7),594:(11,11),595:(6,13),
    596:(6,13),597:(12,8),598:(12,8),599:(8,8),600:(8,8),601:(8,8),
    602:(13,13),603:(13,13),604:(13,13),605:(14,14),606:(14,14),
    607:(7,10),608:(7,10),609:(7,10),610:(16,16),611:(16,16),
    612:(16,16),613:(15,15),614:(15,15),615:(15,15),616:(6,6),
    617:(6,6),618:(4,13),619:(1,1),620:(1,1),621:(16,16),622:(4,7),
    623:(4,7),624:(17,8),625:(17,8),626:(0,0),627:(0,2),628:(0,2),
    629:(17,2),630:(17,2),631:(10,10),632:(6,8),633:(17,16),
    634:(17,16),635:(17,16),636:(6,10),637:(6,10),638:(8,1),
    639:(5,1),640:(12,1),641:(2,2),642:(13,2),643:(16,10),644:(16,13),
    645:(4,2),646:(16,15),647:(11,1),648:(0,14),649:(6,8),
}

# RR-specific type overrides (loaded from data/rr_types.json at import time).
# Covers all 1328 RR species including type changes from vanilla.
import os as _os  # noqa: E402

_RR_TYPES: dict[int, tuple[int, int]] = {}
_rr_types_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                               "data", "games", "gen3_frlge", "rr_types.json")
if _os.path.exists(_rr_types_path):
    import json as _json
    with open(_rr_types_path) as _f:
        _raw_t = _json.load(_f)
        _RR_TYPES = {int(k): tuple(v) for k, v in _raw_t.items()}
    del _json, _f, _raw_t
del _rr_types_path


def species_types(species_id: int, is_rr: bool = False) -> tuple[int, int] | None:
    """Return (type1_byte, type2_byte) for a species, or None if unknown.

    species_id is ALWAYS a CFRU internal ID.
    When is_rr=True, tries RR types first (covers RR-specific type changes),
    then CFRU form types, then NatDex fallback.
    """
    if not species_id or species_id < 1:
        return None
    # RR-specific types (authoritative for Radical Red)
    if is_rr:
        pair = _RR_TYPES.get(species_id)
        if pair:
            return pair
    # CFRU alt-form types
    pair = CFRU_FORM_TYPES.get(species_id)
    if pair:
        return pair
    # NatDex fallback (Gen I–III, species 1–386)
    nat = to_national(species_id)
    return _NATDEX_SPECIES_TYPES.get(nat)


def type_name(type_byte: int) -> str:
    """Return the display name for a Gen III type byte value."""
    return TYPE_NAMES.get(type_byte, "???")


def _parse_pid_otid_key(key: str) -> "tuple[int, int] | None":
    """Parse a PID:OTID key string into (personality, ot_id) integers.

    Returns None if the key is malformed or not a 2-part hex key.
    Used by Gen 3/4/5 adapters and the state.py shiny helper.
    """
    try:
        parts = key.split(":")
        if len(parts) != 2:
            return None
        return int(parts[0], 16), int(parts[1], 16)
    except (ValueError, IndexError):
        return None


def pid_otid_shiny(personality: int, ot_id: int) -> bool:
    """Return True if the Gen III/IV/V shiny formula fires.

    Shiny iff (TID ^ SID ^ upper16(PID) ^ lower16(PID)) < 8.
    """
    tid = ot_id & 0xFFFF
    sid = (ot_id >> 16) & 0xFFFF
    p_upper = (personality >> 16) & 0xFFFF
    p_lower = personality & 0xFFFF
    return (tid ^ sid ^ p_upper ^ p_lower) < 8
