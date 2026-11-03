#!/usr/bin/env python3
"""Username checker engine (no GUI). Used by server.py.
"""

import itertools
import json
import math
import os
import queue
import random
import re
import string
import sys
import threading
import time

import requests

# --------------------------------------------------------------------------
# Platforms. Each checker takes a list of names and returns ({name: status}, wait)
# status is "taken", "avail" or "unknown". Returning (None, seconds) means
# "rate limited / blocked - wait and retry". Unclear answers are "unknown",
# never "avail", so a blocked request can't produce a fake available name.
# --------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _retry_after(r, default=10):
    try:
        return int(r.headers.get("Retry-After", default))
    except ValueError:
        return default


def minecraft_check(session, names):
    r = session.post("https://api.minecraftservices.com/minecraft/profile/lookup/bulk/byname",
                     json=names, timeout=15)
    if r.status_code == 429:
        return None, _retry_after(r)
    if r.status_code != 200:
        return None, 2
    taken = {p["name"].lower() for p in r.json()}
    return {n: ("taken" if n.lower() in taken else "avail") for n in names}, 0


def roblox_check(session, names):
    r = session.post("https://users.roblox.com/v1/usernames/users",
                     json={"usernames": names, "excludeBannedUsers": False}, timeout=15)
    if r.status_code == 429:
        return None, _retry_after(r, 15)
    if r.status_code != 200:
        return None, 3
    taken = {d["requestedUsername"].lower() for d in r.json().get("data", [])}
    return {n: ("taken" if n.lower() in taken else "avail") for n in names}, 0


def tiktok_check(session, names):
    name = names[0]
    r = session.get(f"https://www.tiktok.com/@{name}", headers=BROWSER_HEADERS, timeout=15)
    if r.status_code in (403, 429):
        return None, 30
    txt = r.text
    # Any sign the account exists - including private ones - means the name is taken
    private_markers = ('"statusCode":10222', '"privateAccount":true', '"secret":true',
                       "This account is private")
    if (re.search(r'"uniqueId":"%s"' % re.escape(name), txt, re.I)
            or any(m in txt for m in private_markers)
            or ('"statusCode":0' in txt and '"userInfo"' in txt)):
        return {name: "taken"}, 0
    if '"statusCode":10221' in txt:
        # The page says "not found" - get a second opinion before calling it available.
        # (Pages can wrongly say "not found" for accounts that exist but are restricted.)
        o = session.get("https://www.tiktok.com/oembed", params={"url": f"https://www.tiktok.com/@{name}"},
                        headers=BROWSER_HEADERS, timeout=15)
        if o.status_code in (403, 429):
            return None, 30
        if o.status_code == 200:
            return {name: "taken"}, 0
        if o.status_code in (400, 404):
            return {name: "avail"}, 0
        return {name: "unknown"}, 0
    return {name: "unknown"}, 0


def instagram_check(session, names):
    """Uses the profile-info endpoint Instagram's own website calls.
    (Plain profile-page requests return 200 for everything, so they can't be trusted.)"""
    name = names[0]
    headers = dict(BROWSER_HEADERS)
    headers.update({
        "Accept": "*/*",
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{name}/",
    })
    r = session.get("https://www.instagram.com/api/v1/users/web_profile_info/",
                    params={"username": name}, headers=headers, timeout=15, allow_redirects=False)
    session.last_reason = f"Instagram answered HTTP {r.status_code}"
    if r.status_code == 429:
        return None, 60
    if r.status_code == 404:
        return {name: "avail"}, 0
    if r.status_code == 200:
        try:
            user = (r.json().get("data") or {}).get("user")
        except ValueError:
            return {name: "unknown"}, 0
        if user:
            return {name: "taken"}, 0
    return {name: "unknown"}, 0


def discord_check(session, names):
    """Discord's signup-page lookup (no login needed). Answers {"taken": true/false}."""
    name = names[0]
    headers = dict(BROWSER_HEADERS)
    headers.update({"Content-Type": "application/json", "Origin": "https://discord.com",
                    "Referer": "https://discord.com/register"})
    r = session.post("https://discord.com/api/v9/unique-username/username-attempt-unauthed",
                     json={"username": name}, headers=headers, timeout=15)
    session.last_reason = f"Discord answered HTTP {r.status_code}"
    if r.status_code == 429:
        wait = 10
        try:
            wait = int(float(r.json().get("retry_after", wait))) + 1
        except (ValueError, AttributeError):
            pass
        return None, wait
    if r.status_code == 200:
        try:
            taken = r.json().get("taken")
        except ValueError:
            return {name: "unknown"}, 0
        if taken is True:
            return {name: "taken"}, 0
        if taken is False:
            return {name: "avail"}, 0
    return {name: "unknown"}, 0


def github_check(session, names):
    """Profile page lookup. 404 = nobody (user or organization) has the name. Redirects are not followed:
    a 301 means the name belonged to someone who renamed, so it is still reserved."""
    name = names[0]
    r = session.get(f"https://github.com/{name}", headers=BROWSER_HEADERS, timeout=15, allow_redirects=False)
    session.last_reason = f"GitHub answered HTTP {r.status_code}"
    if r.status_code in (403, 429):
        return None, _retry_after(r, 60)
    if r.status_code == 404:
        return {name: "avail"}, 0
    if r.status_code in (200, 301):
        return {name: "taken"}, 0
    return {name: "unknown"}, 0


PLATFORMS = {
    "Minecraft": {
        "min": 3, "max": 16, "periods": False, "batch": 10, "threads": 8, "delay": 0, "fn": minecraft_check,
        "note": "Uses Mojang's official lookup, 10 names at a time. Mojang doesn't publish which names are locked after a name "
                "change (37 days), so the program remembers names it has seen taken: if one is free later within 37 days, it goes "
                "in the LOCKED panel instead of Available. It only knows names you've scanned before.",
    },
    "Roblox": {
        "min": 3, "max": 20, "periods": False, "batch": 50, "threads": 4, "delay": 0, "fn": roblox_check,
        "note": "Uses Roblox's public username lookup, 50 names at a time. 'Available' means no account has it; "
                "Roblox's own filters may still reject some names when you sign up. One underscore max, not at the start or end.",
    },
    "TikTok": {
        "min": 2, "max": 24, "periods": True, "batch": 1, "threads": 3, "delay": 0.5, "fn": tiktok_check,
        "note": "Unofficial: reads public profile pages one name at a time, so it's much slower, and 'available' is a best "
                "guess - banned, deleted or reserved names can look available but still be refused at signup. Blocked "
                "requests count as Unknown. Keep threads low.",
    },
    "GitHub": {
        "min": 1, "max": 39, "periods": False, "underscore": False, "hyphens": True,
        "batch": 1, "threads": 2, "delay": 2.0, "fn": github_check,
        "note": "Reads public profile pages (users and organizations share names), one at a time. GitHub names use letters, "
                "numbers and single hyphens only (not at the start or end). A free name can still be refused at signup if "
                "it's reserved, or belonged to a suspended or recently renamed account.",
    },
    "Discord": {
        "min": 2, "max": 32, "periods": True, "batch": 1, "threads": 1, "delay": 6.0, "fn": discord_check,
        "note": "Uses the lookup on Discord's signup page, one name at a time. Discord limits this very strictly, so it "
                "runs slowly on purpose (1 thread, about 6s per name - raise the delay if you still get limited). Reserved names (like 'discord' or 'everyone') and filtered words may look "
                "available but be refused at signup.",
    },
    "Instagram": {
        "min": 1, "max": 30, "periods": True, "batch": 1, "threads": 2, "delay": 1.5, "fn": instagram_check,
        "note": "Unofficial and the least reliable: Instagram often hides profiles behind a login. Anything unclear counts "
                "as Unknown, never Available. Slow on purpose - too fast and Instagram blocks your IP for a while.",
    },
}


def valid_name(platform, n):
    """Skip names the platform wouldn't allow anyway."""
    if platform == "Roblox":
        return n.count("_") <= 1 and not n.startswith("_") and not n.endswith("_")
    if platform == "GitHub":
        return not n.startswith("-") and not n.endswith("-") and "--" not in n and "_" not in n and "." not in n
    if platform == "TikTok" and not any(c.isalpha() for c in n):
        return False  # TikTok usernames can't be only numbers (or only _ and .)
    if PLATFORMS[platform]["periods"]:
        return not n.startswith(".") and not n.endswith(".") and ".." not in n
    return True

# --------------------------------------------------------------------------
# Built-in word list. Drop a bigger list called words.txt (one word per line)
# next to the program and it will be loaded too.
# --------------------------------------------------------------------------
WORDS_RAW = """
able acid acorn actor adept adobe agate agent agile alarm album alder alert algae alien alley
alloy almond alpha altar amber ambush amulet anchor angel anger angle ankle antler anvil apex
apple apron arbor arc arch arctic arena argon armor army arrow ash aspen asset atlas atom attic
aura autumn avalanche avatar axe axis azure badge badger bait baker balance bald ballad bamboo
banana band bandit banner baron barrel basalt basil basin bastion bat bay beach beacon beam
bean bear beast beetle bell belt bench berry bird bishop bison blade blast blaze blend bliss
blizzard block bloom blossom blue blunt boar boat bold bolt bomb bone bonus book boom boost
boot border bow bowl brass brave bread breeze brew brick bridge bright brine bronze brook broom
brown brush bubble buck buddy bugle bull bunny burn burst bush butter button cabin cable cactus
cadet cage cake calm camel camp candle candy cane canyon cape captain caramel carbon cargo
carrot cart carve cascade castle cat cave cedar cello chain chalk champ chance chaos charm
chase chef cherry chess chest chief chill chime chip chrome cider cinder circle citrus clan
claw clay clever cliff climb cloak clock cloud clover clown club coal coast cobalt cobra cocoa
code coin comet compass condor copper coral cord core cork corn cosmic cougar crab craft crane
crash crate crazy cream creek creeper crest crew cricket crimson crisp crow crown crumb crush
crystal cube cure curl cyber cycle cyclone dagger daisy dance dark dart dash dawn day dazzle
deal deck deep deer delta demon den desert desk dew dial diamond dingo dino disco dive dock
doctor dodge dog dolphin dome donut door dove dragon drake draw dream drift drill drip drive
drone drop drum duck dude duel dune dusk dust eagle earth east echo eclipse edge eel elder
elf elite elk elm ember emerald empire end energy engine enigma envoy epic equal ether event
evil exile eye fable face fair falcon fall fame fang fantasy farm fate feast feather fern
ferret ferry festival fever field fiend fig fight film finch fine finger fir fire firefly
fish fist flag flair flame flare flash flask fleet flick flint flip float flock flood floor
flora flow flower flute fly foam focus fog foil folk forest forge fork fort fortune fossil
fox frame freak free freeze fresh friend frog frost fruit fudge fuel fury fuse future gadget
gale galaxy gale game gamer gang garden garlic gate gator gaze gear gecko gem genie ghost
giant gift ginger giraffe glacier glade glass gleam glide glimmer globe gloom glory glove
glow glue gnome goat goblin gold golem golf gong goose gorilla grace grain grand granite
grape grass gravel gray grease green grid grim grin grip grizzly groove grove growl guard
guest guide guild guitar gulf gull gust habit hail hair half hall halo hammer hand harbor
hare harp harvest hat haunt haven hawk hazel heart heat heaven hedge heli hello helm hemp
herb hero hickory hide high hill hive hobby hollow holly home honey hood hook hope horn
horse hound house hover howl hub hudson hulk hunt hunter hurricane husky hydra ice icon idea
igloo image impact imp india indigo infinity ink iris iron island ivory ivy jack jade jaguar
jam jar jazz jelly jester jet jewel jinx joker jolly journey joy judge juice jumbo jump
jungle junk jury kale karma kayak keen keep kelp kernel kettle key kick kid kind king kiss
kite kitten kiwi knight knot koala koi lab lace ladder lady lagoon lake lamb lamp lance land
lane lantern lark laser lasso latch laugh lava lawn leaf league leap legend lemon lens leo
leopard level lever light lily lime linen link lion liquid lizard llama loaf lobster lock
lodge logic loom loop loot lord lotus loud love lucky lumber luna lunar lure lush lynx
mace mad magic magma magnet maid mail maker mamba mammoth mango manor maple marble march
mare marsh mask mason mast master match matrix mayor maze meadow medal medic melon melody
mercury mesa metal meteor midnight might mighty mill mimic mind mine mink mint mirage mirror
mist mocha moder modern mole monk monkey monster moon moose moss moth motor mound mount
mouse mud mule mummy muse music mustang myth nacho nano navy neat nebula neon nerd nest net
nether night ninja noble nomad north nova nugget nut nymph oak oasis oat ocean ocelot octo
olive omega omen onyx opal orange orbit orca orchid origin otter outlaw oven owl oxide oyster
pace pack paddle page pain paint palace palm panda panic panther paper parade parrot party
pasta patch path peace peach peak pearl pebble pecan pen penguin pepper perch phantom phoenix
photon piano pickle pie pig pigeon pike pilot pine ping pink pipe pirate pit pivot pixel
pizza plains planet plank plant plasma plate platinum play plaza plum plume plunge pocket
poet point poison polar pole pond pony pool poppy port portal power prairie prank prey pride
prime prince prism pro prowl puck puddle puff puma pump punch pup puppet pure purple puzzle
pyro python quail quake quartz queen quest quick quiet quill quiz rabbit raccoon race radar
radio raft rage rail rain rainbow raptor rapid raven ray razor reach realm rebel reed reef
relic remix rhino rhythm ribbon rice rich ridge rift ring riot ripple rise river road roar
robin robot rock rocket rogue roll roof rook room root rope rose round royal ruby rug rune
rush rust sable sack saddle safe saga sage sail saint salmon salt sand sandy sapphire sash
saturn sauce savage saw scale scar scarf scene scout scrap sea seal seed serpent shade
shadow shaft shake shard shark sharp shell shield shift shine ship shock shoe shore shot
shrimp shrine sickle siege sigma signal silent silk silver simple sing siren sketch skill
skull sky slate sled sleek sleep slice slide slime sling slope sloth slug small smart smash
smile smoke smooth snack snail snake snap snare sniper snow soap socket soda solar soldier
solo sonic soul sound soup south space spark spear speed spell sphinx spice spider spike
spin spirit splash spoon spore sport spot spring sprite spruce spy squad squid squirrel
stable staff stag stage stalker stamp star steam steel stem step stew stick stone storm
story stream street strike string strong studio stump style sugar summer summit sun sunny
super surf surge swamp swan sweet swift swim swing sword symbol tablet tackle tail talon
tango tank tape target tart taste tavern tea teal team tech temple tempest tender tent
terra thief thistle thorn thread throne thumb thunder tick tide tiger tiki timber time tin
tiny titan toad toast token tomato tone tool tooth top topaz torch tornado tower toy trace
track trade trail train trap treasure tree trench tribe trick trio troll trophy truck
trumpet trunk trust tulip tundra tuna turbo turkey turtle tusk twig twilight twin twist
ultra umbra uncle union unity urban urchin valley valor vampire vanilla vapor vault vector
velvet venom venus verse vessel vex viking village vine violet viper virtue vision vivid
void volcano volt vortex voyage vulture wafer wagon waker wall walrus wand wander warden
warp warrior wasp watch water wave wax wealth weasel web wedge weed welder west whale wheat
wheel whirl whisk whisper widget wild willow wind wine wing winter wire wisdom wisp witch
wizard wolf wolverine wonder wood wool world worm wraith wren yak yard yarn yeti yield
yoga yolk youth zap zeal zebra zen zenith zero zest zinc zip zodiac zone zoom zombie
anger arrow bacon beta blitz bravo breach brisk cipher clutch cobweb cosmos cruiser dagger
dynamo ember flicker frenzy gambit glitch gizmo grunt hyper impulse jolt kraken lancer
legion lunar maverick mystic nitro nexus oracle omen outpost patrol phase pulse quasar
radiant reaper rumble saber sentry shatter sizzle spectre stealth stinger surge tactic
talisman tempo thrash titan trance turret ultra vandal vengeance vertex vigil viper warden
whiplash wrath xenon yonder zephyr
"""


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_words():
    words = {w for w in WORDS_RAW.lower().split()}
    extra = os.path.join(app_dir(), "words.txt")
    if os.path.exists(extra):
        try:
            with open(extra, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    words.add(line.strip().lower())
        except OSError:
            pass
    return {w for w in words if w.isascii() and w.isalpha() and 3 <= len(w) <= 16}


WORDS = load_words()

# --------------------------------------------------------------------------
# Locked-name tracking (Minecraft). Mojang's public lookup only says whether a name
# is in use RIGHT NOW - it has no data on names held after a name change. So the
# program remembers every name it has seen taken. A name seen taken within the last
# 37 days that is now free was most likely just released by its owner and is still
# inside the 37-day hold. History is saved next to the program.
# --------------------------------------------------------------------------
LOCK_DAYS = 37
DAY = 86400


def history_path():
    return os.path.join(app_dir(), "minecraft_name_history.json")


def load_history():
    try:
        with open(history_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 2}
    except (OSError, ValueError, AttributeError):
        return {}


def save_history(hist):
    path = history_path()
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(hist, fh, separators=(",", ":"))
        os.replace(path + ".tmp", path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# "Rare name" detection - is this name a word, or at least word-like (not junk)?
#
# Tiers (lower = better):
#   1 real word                      fire
#   2 two words joined               firewolf / fire_wolf
#   3 real word + numbers/underscore wolf99 / _wolf
#   4 near-word / hides a word / anagram       wolfz, xbearx, anagram of a word
#   5 cool patterns + short pronounceable      ennv, abba, abab, abcd, zorv, kelo, jewz
#   6 longer pronounceable name                velmora, kelo_7
# --------------------------------------------------------------------------
LEVELS = {
    "Strict (real words only)": 2,
    "Normal (words, word-like, patterns)": 5,
    "Loose (anything pronounceable)": 6,
}

START2 = set("bl br ch cl cr dr dw fl fr gl gn gr kl kn kr ph pl pr ps sc sh sk sl sm sn sp sq st sw th tr tw wh wr".split())
START3 = set("str spr spl scr shr thr chr sch sph".split())
END2 = set("ck ct ft ld lf lk lm lp lt mp nd ng nk nt pt rb rd rk rl rm rn rp rt sk sp st sh ch th ph gh "
           "ds ts ks ps gs ns ls rs ms bs fs lb lv rv rf xt".split())
END3 = set("nds nts rts rks sts lts mps nks cks ngs ths rds rns rms rls rps lds lks lms lps fts sks sps "
           "rst lst nst nch tch rch lch rth nth lth mpt nct ght rld rnt".split())
MID2 = START2 | END2 | set("rc rg rz lc lg ls mb ms nc nf nv nz sb sd sf sg bt bd dg dm dn gm kt ks cs tz dz zl".split())
MID3 = START3 | END3 | set("ndr ntr mbl ngl ngr mbr ndl ntl rtr lpt nkl rkl dsh".split())
GEMINATES = set("lsfzdtnmrgpkb")
SUFFIXES = ("ez", "ie", "ee", "er", "ly", "zz", "xx", "ss", "ix", "oz", "ox", "ey")


VOWEL_PAIRS = set("ai ay au aw ea ee ei eu ew ey ia ie io iu oa oi oo ou ow oy ue ui ua uo".split())


def _is_vowel(s, i):
    c = s[i]
    if c in "aeiou":
        return True
    if c == "w":
        return i > 0 and s[i - 1] in "aeo"  # aw / ew / ow
    if c == "y":
        return i > 0 and not (i + 1 < len(s) and s[i + 1] in "aeiou")
    return False


def _cons_ok(run, at_start, at_end):
    n = len(run)
    if n == 1:
        return True
    if n == 2:
        if run[0] == run[1]:
            return run[0] in GEMINATES and not at_start
        if at_start:
            return run in START2
        if at_end:
            return run in END2 or (run[1] in "sz" and run[0] not in "sz")
        return run in MID2
    if n == 3:
        if at_start:
            return run in START3
        if at_end:
            return run in END3
        return run in MID3
    return False


def pronounceable(s):
    """Rough check: could you say it out loud? (no 'bwno', 'xqzt', 'kcat')"""
    n = len(s)
    if n < 3 or n > 12 or not s.isalpha():
        return False
    if re.search(r"(.)\1\1", s) or "yy" in s or s[-1] in "jq":
        return False
    if re.search(r"q(?!u)", s):
        return False
    flags = [_is_vowel(s, i) for i in range(n)]
    if not any(flags):
        return False
    i = 0
    while i < n:
        j = i
        while j < n and flags[j] == flags[i]:
            j += 1
        if flags[i]:
            run = s[i:j]
            if len(run) > 3 or any(run[k:k + 2] not in VOWEL_PAIRS for k in range(len(run) - 1)):
                return False
        elif not _cons_ok(s[i:j], i == 0, j == n):
            return False
        i = j
    return True


def _neighbors(s, substitute=True):
    """Every string one edit away (delete, replace, insert, swap)."""
    letters = string.ascii_lowercase
    out = set()
    for i in range(len(s)):
        out.add(s[:i] + s[i + 1:])
        if substitute:
            for c in letters:
                out.add(s[:i] + c + s[i + 1:])
    for i in range(len(s) + 1):
        for c in letters:
            out.add(s[:i] + c + s[i:])
    for i in range(len(s) - 1):
        out.add(s[:i] + s[i + 1] + s[i] + s[i + 2:])
    out.discard(s)
    return out


def _compound(s):
    for i in range(3, len(s) - 2):
        if s[:i] in WORDS and s[i:] in WORDS:
            return f"{s[:i]} + {s[i:]}"
    return None


ANAGRAMS = {}
for _w in WORDS:
    ANAGRAMS.setdefault("".join(sorted(_w)), []).append(_w)


def _contains_word(n):
    """Longest real word (4+ letters) hidden inside the name with at most 3 extra characters."""
    for size in range(len(n) - 1, 3, -1):
        if len(n) - size > 3:
            break
        for i in range(len(n) - size + 1):
            if n[i:i + size] in WORDS:
                return n[i:i + size]
    return None


def _pattern(n):
    """Cool-looking structure: repeats, doubles, palindromes, sequences. Returns a label or None."""
    core = n.replace("_", "").replace(".", "").replace("-", "")
    if len(core) < 3 or not core.isalnum() or not any(c.isalpha() for c in core):
        return None
    if len(set(core)) == 1:
        return "all the same character"
    if len(core) >= 4 and core == core[::-1]:
        return "palindrome"
    for size in range(2, len(core) // 2 + 1):
        if len(core) % size == 0 and core == core[:size] * (len(core) // size):
            return f"repeating '{core[:size]}'"
    for i in range(len(core) - 2):
        a, b, c = core[i:i + 3]
        if a.isalpha() and b.isalpha() and c.isalpha() and (
                ord(b) - ord(a) == ord(c) - ord(b) == 1 or ord(a) - ord(b) == ord(b) - ord(c) == 1):
            return f"letter sequence ({a}{b}{c})"
    doubles = re.findall(r"([a-z])\1", core)
    if len(doubles) >= 2:
        return "two double letters"
    if doubles:
        return f"double letter ({doubles[0] * 2})"
    return None


def classify(name):
    """Returns (tier, label). tier 0 = junk."""
    n = name.lower()
    if n in WORDS:
        return 1, "word"

    parts = re.split(r"[_.\-]", n)
    if len(parts) == 2 and all(p in WORDS for p in parts):
        return 2, f"{parts[0]} + {parts[1]}"
    if n.isalpha() and len(n) >= 6:
        c = _compound(n)
        if c:
            return 2, c

    core = n.strip("0123456789_.-")
    decorated = core != n and len(core) >= 3
    if decorated and core in WORDS:
        return 3, f"{core} + extra"

    if n.isalpha() and 4 <= len(n) <= 12:
        # short names need to sound like something; longer ones are only flagged
        # if they sit one typo away from a real word (so "wolfz", "pixl" count)
        if len(n) >= 5 or pronounceable(n):
            for suf in SUFFIXES:
                if n.endswith(suf) and n[: -len(suf)] in WORDS:
                    return 4, f"{n[: -len(suf)]} + {suf}"
            near = sorted(w for w in _neighbors(n, substitute=len(n) >= 5) if w in WORDS)
            if near:
                return 4, f"looks like {near[0]}"

    if n.isalpha() and 5 <= len(n) <= 12:
        inner = _contains_word(n)
        if inner:
            return 4, f"contains '{inner}'"
    if n.isalpha() and 4 <= len(n) <= 10:
        ana = [w for w in ANAGRAMS.get("".join(sorted(n)), []) if w != n]
        if ana:
            return 4, f"anagram of {sorted(ana)[0]}"

    pat = _pattern(n)
    if pat:
        return 5, pat

    if n.isalpha() and 3 <= len(n) <= 5 and pronounceable(n):
        return 5, "pronounceable"
    if n.isalpha() and 6 <= len(n) <= 10 and pronounceable(n):
        return 6, "pronounceable"
    if decorated and len(core) <= 8 and pronounceable(core):
        return 6, "pronounceable + extra"
    return 0, ""


def _patterns(n):
    """Every visual pattern the name has, as (category, detail) pairs."""
    core = n.replace("_", "").replace(".", "").replace("-", "")
    out = []
    if len(core) < 3 or not core.isalnum() or not any(c.isalpha() for c in core):
        return out
    if len(set(core)) == 1:
        out.append(("repeat", f"all {core[0]}"))
    else:
        for size in range(2, len(core) // 2 + 1):
            if len(core) % size == 0 and core == core[:size] * (len(core) // size):
                out.append(("repeat", f"repeats {core[:size]}"))
                break
    if len(core) >= 4 and core == core[::-1]:
        out.append(("palindrome", ""))
    for i in range(len(core) - 2):
        a, b, c = core[i:i + 3]
        if a.isalpha() and b.isalpha() and c.isalpha() and (
                ord(b) - ord(a) == ord(c) - ord(b) == 1 or ord(a) - ord(b) == ord(b) - ord(c) == 1):
            out.append(("sequence", a + b + c))
            break
    doubles = re.findall(r"([a-z0-9])\1", core)
    if doubles:
        out.append(("double", " ".join(d * 2 for d in doubles[:2])))
    return out


def categorize(name):
    """Every rare category a name belongs to, best first, as [category, detail] pairs.
    The app decides which categories count as rare (the user can change that at any time)."""
    n = name.lower()
    L = len(n)
    alpha = n.isalpha()
    out = []
    if L <= 3:
        out.append(["og3", ""])
    is_word = n in WORDS
    if is_word:
        out.append(["word", ""])
    if L == 4 and alpha:
        out.append(["og4", ""])

    parts = re.split(r"[_.\-]", n)
    if len(parts) == 2 and all(p in WORDS for p in parts):
        out.append(["compound", f"{parts[0]} + {parts[1]}"])
    elif alpha and L >= 6 and not is_word:
        c = _compound(n)
        if c:
            out.append(["compound", c])

    core = n.strip("0123456789_.-")
    decorated = core != n and len(core) >= 3
    if decorated and core in WORDS:
        out.append(["word_extra", f"{core} + {n[len(core):] if n.startswith(core) else n[:n.find(core)]}"])

    if alpha and not is_word and 4 <= L <= 12 and (L >= 5 or pronounceable(n)):
        near = None
        for suf in SUFFIXES:
            if n.endswith(suf) and n[: -len(suf)] in WORDS:
                near = f"{n[: -len(suf)]} + {suf}"
                break
        if not near:
            hits = sorted(w for w in _neighbors(n, substitute=L >= 5) if w in WORDS)
            if hits:
                near = f"looks like {hits[0]}"
        if near:
            out.append(["near", near])
    if alpha and not is_word and 4 <= L <= 10:
        ana = [w for w in ANAGRAMS.get("".join(sorted(n)), []) if w != n]
        if ana:
            out.append(["anagram", f"of {sorted(ana)[0]}"])
    if alpha and not is_word and 5 <= L <= 12:
        inner = _contains_word(n)
        if inner:
            out.append(["hidden", f"contains {inner}"])

    out.extend([list(p) for p in _patterns(n)])

    if alpha and not is_word and 3 <= L <= 5 and pronounceable(n):
        out.append(["say_short", ""])
    elif alpha and not is_word and 6 <= L <= 10 and pronounceable(n):
        out.append(["say_long", ""])
    elif decorated and len(core) <= 8 and core not in WORDS and pronounceable(core):
        out.append(["say_long", f"{core} + extra"])
    return out


# --------------------------------------------------------------------------
# Name generation
# --------------------------------------------------------------------------
def brute_force_names(charset, min_len, max_len, randomize, limit):
    total = sum(len(charset) ** n for n in range(min_len, max_len + 1))
    limit = min(limit, total)
    if randomize:
        seen = set()
        lengths = list(range(min_len, max_len + 1))
        while len(seen) < limit:
            n = random.choice(lengths)
            name = "".join(random.choice(charset) for _ in range(n))
            if name not in seen:
                seen.add(name)
                yield name
    else:
        count = 0
        for n in range(min_len, max_len + 1):
            for combo in itertools.product(charset, repeat=n):
                yield "".join(combo)
                count += 1
                if count >= limit:
                    return


def keyword_names(kw, charset, min_len, max_len, randomize, limit):
    """Every name containing `kw`, padded with characters from `charset` before and/or after it.
    e.g. kw='larp', length 5 -> 1larp, alarp, larp1, larpa ... ; length 6 -> 1larp1, ab larp ..."""
    k = len(kw)
    lo = max(min_len, k)
    if lo > max_len:
        return
    c = len(charset)
    lengths = list(range(lo, max_len + 1))
    space = [(L - k + 1) * c ** (L - k) for L in lengths]  # upper bound (ignores repeats)
    if randomize:
        seen = set()
        goal = min(limit, sum(space))
        tries = 0
        while len(seen) < goal and tries < goal * 20 + 1000:
            tries += 1
            L = random.choices(lengths, weights=[float(x) for x in space])[0]
            extra = L - k
            a = random.randint(0, extra)
            fill = [random.choice(charset) for _ in range(extra)]
            name = "".join(fill[:a]) + kw + "".join(fill[a:])
            if name not in seen:
                seen.add(name)
                yield name
    else:
        for L in lengths:
            extra = L - k
            for a in range(extra + 1):
                for fill in itertools.product(charset, repeat=extra):
                    name = "".join(fill[:a]) + kw + "".join(fill[a:])
                    if name.find(kw) == a:  # skip repeats of a name already produced
                        yield name


def word_names(min_len, max_len, randomize, limit):
    names = [w for w in WORDS if min_len <= len(w) <= max_len]
    if randomize:
        random.shuffle(names)
    else:
        names.sort()
    return iter(names[:limit])


# --------------------------------------------------------------------------
# Worker threads
# --------------------------------------------------------------------------
class Checker:
    def __init__(self, names, threads, out_q, stop_evt, rare_max, platform, delay=None):
        self.names = names
        self.rare_max = rare_max
        self.threads = threads
        self.q = out_q
        self.stop = stop_evt
        self.spec = PLATFORMS[platform]
        self.delay = self.spec["delay"] if delay is None else delay
        self.platform = platform
        self.lock = threading.Lock()
        self.pause_until = 0.0   # shared cooldown: one rate limit pauses every thread
        self.penalty = 0.0       # extra delay added after rate limits, shrinks while things go well

    def start(self):
        workers = [threading.Thread(target=self.worker, daemon=True) for _ in range(self.threads)]
        for w in workers:
            w.start()

        def monitor():
            for w in workers:
                w.join()
            self.q.put(("done", None))

        threading.Thread(target=monitor, daemon=True).start()

    def worker(self):
        session = requests.Session()
        while not self.stop.is_set():
            with self.lock:
                batch = list(itertools.islice(self.names, self.spec["batch"]))
            if not batch:
                break
            # honor a cooldown started by any thread
            while not self.stop.is_set() and time.time() < self.pause_until:
                self.stop.wait(0.25)
            self.check(session, batch)
            pause = self.delay + self.penalty
            if pause:
                self.stop.wait(pause)

    def check(self, session, batch):
        fn = self.spec["fn"]
        for attempt in range(6):
            if self.stop.is_set():
                return
            try:
                results, wait = fn(session, batch)
            except (requests.RequestException, ValueError, KeyError):
                self.q.put(("status", "Network problem, retrying..."))
                self.stop.wait(1 + attempt)
                continue

            if results is None:
                wait = max(wait, 5 + attempt * 5)
                if wait > 300:
                    mins = round(wait / 60)
                    self.q.put(("notice", f"{self.platform} rate limited you for about {mins} minutes, so the run was "
                                          "stopped instead of waiting. Try again later with a longer delay between checks."))
                    self.stop.set()
                    return
                with self.lock:
                    self.pause_until = max(self.pause_until, time.time() + wait)
                    self.penalty = min(self.penalty + 0.25, 5.0)  # slow down after being limited
                self.q.put(("status", f"Rate limited - all threads pausing {wait}s, then running slower"))
                while not self.stop.is_set() and time.time() < self.pause_until:
                    self.stop.wait(0.25)
                continue

            any_unknown = False
            for n in batch:
                state = results.get(n, "unknown")
                if state == "taken":
                    self.q.put(("taken", n))
                elif state == "avail":
                    self.q.put(("avail", (n, categorize(n))))
                else:
                    self.q.put(("skipped", 1))
                    any_unknown = True
            self.penalty = max(0.0, self.penalty - 0.02)  # slowly speed back up
            reason = getattr(session, "last_reason", "")
            if any_unknown and reason:
                self.q.put(("status", f"Unknown result - {reason} (login wall or block?)"))
            else:
                self.q.put(("status", "Running..."))
            return

        self.q.put(("skipped", len(batch)))
