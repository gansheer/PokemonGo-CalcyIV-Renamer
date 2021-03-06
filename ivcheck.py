from pokemonlib import PokemonGo
import yaml
import asyncio
import re
import argparse
import logging
import operator
from sys import platform

def in_func(a, b):
    return a in b

ops = {
    'lt': operator.lt,
    'le': operator.le,
    'eq': operator.eq,
    'ne': operator.ne,
    'ge': operator.ge,
    'gt': operator.gt,
    'in': in_func,
}

logger = logging.getLogger('ivcheck')
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

RE_CALCY_IV = re.compile(r"^./MainService\(\s*\d+\): Received values: Id: \d+ \((?P<name>.+)\), Nr: (?P<id>\d+), CP: (?P<cp>\-{0,1}\d+), Max HP: (?P<max_hp>\d+), Dust cost: (?P<dust_cost>\d+), Level: (?P<level>\-{0,1}[0-9\.]+), FastMove (?P<fast_move>.+), SpecialMove (?P<special_move>.+), Gender (?P<gender>\d), Level-up (true|false):$")
RE_RED_BAR = re.compile(r"^.+\(\s*\d+\): Screenshot #\d has red error box at the top of the screen$")
RE_SUCCESS = re.compile(r"^.+\(\s*\d+\): calculateScanOutputData finished after \d+ms$")
RE_SCAN_INVALID = re.compile(r"^.+\(\s*\d+\): Scan invalid$")


CALCY_SUCCESS = 0
CALCY_RED_BAR = 1
CALCY_SCAN_INVALID = 2

class Main:
    def __init__(self, args):
        with open(args.config, "r") as f:
            self.config = yaml.load(f)
        self.args = args
        self.use_fallback_screenshots = False
        self.iv_regexes = [re.compile(r) for r in self.config["iv_regexes"]]

    async def screencap(self):
        if not self.use_fallback_screenshots:
            return_code, stdout, stderr = await self.run(["adb", "-s", await self.get_device(), "exec-out", "screencap", "-p"])
            try:
                return Image.open(BytesIO(stdout))
            except (OSError, IOError):
                logger.debug("Screenshot failed, using fallback method")
                self.use_fallback_screenshots = True
        return_code, stdout, stderr = await self.run(["adb", "-s", await self.get_device(), "shell", "screencap", "-p", "/sdcard/screen.png"])
        return_code, stdout, stderr = await self.run(["adb", "-s", await self.get_device(), "pull", "/sdcard/screen.png", "."])
        image = Image.open("screen.png")
        return image

    async def tap(self, location):
        await self.p.tap(*self.config['locations'][location])
        if location in self.config['waits']:
            await asyncio.sleep(self.config['waits'][location])

    async def swipe(self, location, duration):
        await self.p.swipe(
            self.config['locations'][location][0],
            self.config['locations'][location][1],
            self.config['locations'][location][0],
            self.config['locations'][location][1],
            duration
        )
        if location in self.config['waits']:
            await asyncio.sleep(self.config['waits'][location])   

    async def start(self):
        self.p = PokemonGo()
        await self.p.set_device(self.args.device_id)
        await self.p.start_logcat()
        num_errors = 0
        while True:
            blacklist = False
            state, values = await self.check_pokemon()
            await self.p.seek_to_end() # just in case any additional lines are present
            if values["name"] in self.config["blacklist"]:
                blacklist = True
            elif state == CALCY_SUCCESS:
                num_errors = 0
            elif state == CALCY_RED_BAR:
                continue
            elif state == CALCY_SCAN_INVALID:
                num_errors += 1
                if num_errors < args.max_retries:
                    continue
                num_errors = 0

            values["success"] = True if state == CALCY_SUCCESS else False
            values["blacklist"] = blacklist

            actions = await self.get_name(values)

            if "rename" in actions or "rename-calcy" in actions:
                if values["success"] is False:
                    await self.tap('close_calcy_dialog') # it gets in the way
                await self.tap('rename')
                if "rename-calcy" in actions:
                    if args.touch_paste:
                        await self.swipe('edit_box', 600)
                        await self.tap('paste')

                    else:
                        await self.p.key(279) # Paste into rename
                elif "rename" in actions:
                    await self.p.text(actions["rename"])

                await self.tap('keyboard_ok')
                await self.tap('rename_ok')
            if "favorite" in actions:
                if await self.check_favorite():
                    await self.tap('favorite_button')
            await self.tap('next')


    async def get_data_from_clipboard(self):
        clipboard = await self.p.get_clipboard()

        for iv_regex in self.iv_regexes:
            match = iv_regex.match(clipboard)
            if match:
                d = match.groupdict()
                if "iv" in d:
                    d["iv"] = float(d["iv"])
                    d["iv_min"] = d["iv"]
                    d["iv_max"] = d["iv"]
                else:
                    for key in ["iv_min", "iv_max"]:
                        if key in d:
                            d[key] = float(d[key])
                    d["iv"] = None
                return d

        raise Exception("Clipboard regex did not match")

    async def check_favorite(self):
        """
        Not the best check, just search the area
        for pixels that are the right color
        """ 
        screencap = await self.p.screencap()
        crop = screencap.crop(self.config['locations']['favorite_button_box'])
        rgb_im = crop.convert('RGB')
        width, height = rgb_im.size
        colors = [
            (244, 192, 13),
            (239, 182, 8),
            (246, 193, 14),
            (240, 184, 9),
            (248, 198, 16),
            (241, 184, 10),
            (243, 188, 11),
            (244, 191, 13),
            (242, 188, 11),
            (242, 186, 10),
            (243, 189, 11),
            (244, 191, 12),
            (243, 189, 12),
            (241, 186, 10),
            (247, 197, 15),
            (247, 196, 15),
            (244, 190, 12),
            (245, 193, 13),
            (246, 194, 14),
            (246, 195, 14),
            (241, 185, 10),
            (240, 183, 9),
            (242, 187, 11),
            (245, 192, 13),
        ]
        color_count = 0
        for x in range(1, width):
            for y in range(1, height):
                c = rgb_im.getpixel((x, y))
                if c in colors:
                    color_count += 1
        return color_count > 500

    async def get_name(self, values):
        clipboard_values = None
        valid_conditions = ["name", "iv", "iv_min", "iv_max", "success", "blacklist"]
        clipboard_required = ["iv", "iv_min", "iv_max"]
        for ruleset in self.config["actions"]:
            conditions = ruleset.get("conditions", {})
            # Check if we need to read the clipboard
            passed = True
            for key, item in conditions.items():
                operator = None
                if "__" in key:
                    key, operator = key.split("__")
                if key in clipboard_required:
                    clipboard_values = await self.get_data_from_clipboard()
                    values = {**values, **clipboard_values}
                if key not in valid_conditions:
                    raise Exception("Unknown Condition {}".format(key))
                if key not in values:
                    passed = False
                    break
                if operator is not None:
                    if operator not in ops:
                        raise Exception("Unknown operator {}".format(operator))
                    operation = ops.get(operator)
                    if not operation(values[key], item):
                        passed = False
                        break
                elif values[key] != conditions[key]:
                    passed = False
                    break
            if passed:
                print("MATCHED", ruleset)
                return ruleset.get("actions", {})
        raise Exception("No action matched")

    async def check_pokemon(self):
        await self.p.send_intent("intent:#Intent\;action=tesmath.calcy.ACTION_ANALYZE_SCREEN\;B.silentMode=true\;end", "tesmath.calcy/.IntentReceiver")
        red_bar = False
        values = {}
        while True:
            line = await self.p.read_logcat()

            match = RE_CALCY_IV.match(line)
            if match:
                logger.debug("RE_CALCY_IV matched")
                result = match.groupdict()
                state = CALCY_SUCCESS
                if "-1" in [result["cp"], result["level"]]:
                    state = CALCY_SCAN_INVALID
                if red_bar is True:
                    state = CALCY_RED_BAR
                return state, result

            match = RE_RED_BAR.match(line)

            if match:
                logger.debug("RE_RED_BAR matched")
                red_bar = True

            match = RE_SCAN_INVALID.match(line)
            if match:
                if red_bar:
                    logger.debug("RE_SCAN_INVALID matched and red_bar is True")
                    return CALCY_RED_BAR, values
                else:
                    logger.debug("RE_SCAN_INVALID matched, raising CalcyIVError")
                    return CALCY_SCAN_INVALID, values
        
if __name__ == '__main__':
    if platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    parser = argparse.ArgumentParser(description='Pokemon go renamer')
    parser.add_argument('--device-id', type=str, default=None,
                        help="Optional, if not specified the phone is automatically detected. Useful only if you have multiple phones connected. Use adb devices to get a list of ids.")
    parser.add_argument('--max-retries', type=int, default=5,
                        help="Maximum retries, set to 0 for unlimited.")
    parser.add_argument('--config', type=str, default="config.yaml",
                        help="Config file location.")
    parser.add_argument('--touch-paste', default=False, action='store_true',
                        help="Use touch instead of keyevent for paste.")
    parser.add_argument('--pid-name', default=None, type=str,
                        help="Create pid file")
    parser.add_argument('--pid-dir', default=None, type=str,
                        help="Change default pid directory")
    args = parser.parse_args()
    if args.pid_name is not None:
        from pid import PidFile
        with PidFile(args.pid_name, args.pid_dir) as p:
            asyncio.run(Main(args).start())
    else:
        asyncio.run(Main(args).start())
