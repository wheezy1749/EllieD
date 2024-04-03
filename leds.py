#!/usr/bin/python3
import socket
import time
import subprocess
import threading
import json
import os
from collections import defaultdict 
import signal
import sys
from rpi_rf import RFDevice
from pathlib import Path
from datetime import datetime
import builtins
import simpleaudio as sa
import wave
import atexit

global test_mode
test_mode = False

subprocess.call(["/usr/local/bin/join.py", "--text", "'led.py restarted'"])
play_lock = threading.Lock()
dir_path = os.path.dirname(os.path.realpath(__file__))
dir_sounds = os.path.join(dir_path, "sounds")
log_path = os.path.join(dir_path, "logs")
#remove old logs except last 10
os.system("ls -1rt -d -1 /root/EllieD/logs/* | head -n -10 | xargs -d '\n' rm -f --")
Path(log_path).mkdir(parents=True, exist_ok=True)
log_time_start = time.time()

def round5(x):
    return 5 * round(x/5)

def onClose():
    subprocess.call(["/usr/local/bin/join.py", "--text", "leds.py crashed"])
    pass

atexit.register(onClose)

def create_new_log():
    global logfile_unique
    logfile_unique = datetime.now().strftime("%Y.%m.%d.%H.%M.%S.log")
    logfile_unique = os.path.join(log_path, logfile_unique)

create_new_log()

def print(*args, **kwargs):
    global logfile_unique
    global log_time_start

    if (time.time() - log_time_start) > 86400:
        create_new_log()
        log_time_start = time.time()

    logf = open(logfile_unique, 'a')
    stamp = datetime.now().strftime("[%Y.%m.%d.%H.%M.%S] ")
    logf.write(stamp)
    builtins.print(*args, **kwargs, file=logf)
    logf.close()
    sys.stdout.write(stamp)
    builtins.print(*args, **kwargs)

def find_client_ip(mac):
    ip = ""
    cmd = f"arp-scan --destaddr={mac} --localnet | grep 192 | tail -1"
    try:
        ip = subprocess.check_output((cmd),shell=True,stderr=subprocess.STDOUT).strip().split()[0].decode("utf-8")
    except (IndexError, subprocess.CalledProcessError):
        print(f"No device found with mac: {mac}")

    if "invalid" in ip.lower():
        return ""

    return ip

def load_wave(path):
    with wave.open(path, 'rb') as wav_file:
        audio_data = wav_file.readframes(wav_file.getnframes())
    return audio_data


led_lock = threading.Lock()
lamp_lock = threading.Lock()
lock_fade_request = threading.Lock()
pwm_pin = 12

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    ENDC = '\033[0m'


class Timer(object):
    def __init__(self, name, min_time=0):
        self.name = name
        self.min_time = min_time

    def __enter__(self):
        self.tstart = time.time()

    def __exit__(self, type, value, traceback):
        duration = time.time() - self.tstart
        if duration > self.min_time:
            if duration > 2:
                color = bcolors.FAIL
            elif duration > 1:
                color = bcolors.WARNING
            else:
                color = bcolors.OKGREEN

            print(f"{color}[{self.name}][{duration:.3f} seconds][{bcolors.ENDC}]")

class LightClients:

    delay_levels = [(30, "30s.mp3"),
                    (1*60, "1m.mp3"),
                    (2*60, "2m.mp3"),
                    (5*60, "5m.mp3"),
                    (10*60,"10m.mp3"),
                    (30*60,"30m.mp3"),
                    (1*60*60,"1h.mp3"),
                    (2*60*60,"2h.mp3"),
                    (3*60*60,"3h.mp3"),
                    (6*6*60,"6h.mp3"),
                    (12*60*60,"12h.mp3")]
    sound_up = os.path.join(dir_path,"sounds/up.wav")
    sound_down = os.path.join(dir_path,"sounds/down.wav")
    sound_bad_input = os.path.join(dir_path,"sounds/bad_input.wav")
    settings_path = os.path.join(dir_path,'settings.json')
    LAMP_CLIENT_MAC = "E0:5A:1B:79:8D:88"
    MOTION_CLIENT_MAC = "08:B6:1F:81:D8:C4"
    LD2410_CLIENT_MAC = "08:B6:1F:81:6D:E0"
    pi = None

    def __init__(self):
        self.IR_CLIENT_IP = "192.168.50.140"
        self.PWM_CLIENT_IP = "192.168.50.60"
        self.MOTION_CLIENT_IP = "192.168.50.54"
        self.LAMP_CLIENT_IP = "192.168.50.220"
        self.LD2410_CLIENT_IP = "192.168.50.221"

        if os.path.exists(self.settings_path):
            self.load_settings()
        else:
            #defaults
            print("No saved settings. Loading default values")
            self._brightness = 20
            self._lamp_brightness = self._brightness
            self._delay = 0
            self._motion_enabled = True
            self._volume = 70

        self._power_state = True
        self._motion_timer = time.time() + 10 #add 10 seconds so no timeout will happen on reboots or power outages
        self.remote_delay = 0.25
        self.last_remote_time = 0
        self.last_pwm_brightness_set = self._brightness
        self.off_because_of_motion = False

        if self._power_state:
            self._setPWMBrightness(self._brightness)
            self.fade_lamp(1,self._lamp_brightness)


    def increase_volume(self):
        volume = self._volume
        volume += 5
        if volume < 60:
            volume = 60

        if volume > 100:
            return False

        subprocess.run(["amixer", "sset","'Speaker'", str(volume) + '%'])
        self._volume = volume
        print("volume = " + str(self._volume))
        return True

    def decrease_volume(self):
        volume = self._volume
        volume -= 5
        if volume < 60:
            subprocess.run(["amixer", "sset","'Speaker'", '0%'])
            return False

        subprocess.run(["amixer", "sset","'Speaker'", str(volume) + '%'])
        self._volume = volume
        print("volume = " + str(self._volume))
        return True

    def handle_remote_event(self, event):
        if time.time() - self.last_remote_time < self.remote_delay:
            print(f"Ignoring input {event} happened too soon after last remote event")
            return

        self.last_remote_time = time.time()

        if event == "POWER_BUTTON":
            if self._power_state:
                print("TURNING OFF")
                if led_lock.locked() or lamp_lock.locked():
                    return None

                self._power_state = False
                self.fade_leds(1, 0, on_off_event=True)
                self.fade_lamp(1, 0, on_off_event=True)
            else:
                print("TURNING ON")
                if led_lock.locked() or lamp_lock.locked():
                    return None
                self._power_state = True
                self.fade_leds(1, self._brightness, on_off_event=True)
                self.fade_lamp(1, self._lamp_brightness, on_off_event=True)

        elif event == "STOP_BUTTON":
            if self._motion_enabled:
                self.disable_motion()
            else:
                self.enable_motion()

        elif event == "BRIGHTNESS_UP":
            if not self._power_state:
                self._power_state = True

            level = min(100, round5(self._brightness + 10))

            if not self.fade_leds(0, level):
                return None

        elif event == "BRIGHTNESS_DOWN":
            level = max(0, self._brightness - 10)

            if not self.fade_leds(0, level):
                return None

        elif event == "BRIGHTNESS_MIN":
            if not self.fade_leds(1, 1):
                return None
            if not self.fade_lamp(1, 5):
                return None
        elif event == "BRIGHTNESS_75":
            if not self.fade_leds(1, 60):
                return None
            if not self.fade_lamp(1, 60):
                return None
        elif event == "DELAY_30S":
            self._delay = 0
        elif event  == "DELAY_10M":
            self._delay = 4
        elif event  == "DELAY_1H":
            self._delay = 6
        elif event  == "DELAY_3H":
            self._delay = 8
        elif event  == "LAMP_UP":
            if not self._power_state:
                self._power_state = True

            if lamp_lock.locked():
                return None
            if self._lamp_brightness <= 80:
                if not self.fade_lamp(0, self._lamp_brightness + 10):
                    return None
        elif event  == "LAMP_DOWN":
            if lamp_lock.locked():
                return None
            if self._lamp_brightness >= 10:
                self.fade_lamp(0, self._lamp_brightness - 10)
            else:
                event = "BAD_INPUT"
        elif event == "VOLUME_UP":
            if not self.increase_volume():
                event = "BAD_INPUT"
        elif event == "VOLUME_DOWN":
            if not self.decrease_volume():
                event = "BAD_INPUT"

        elif event[:3] == "IR_":
            sock.sendto(event.encode(), (self.IR_CLIENT_IP, UDP_PORT));

        self.alert(event)
        self.save_settings()
        time.sleep(0.05)

    def handle_motion_event(self, event):

        if not self._motion_enabled:
            if event == "MOTION_DETECTED":
                self._motion_timer = time.time()
            motionless_time = time.time() - self._motion_timer
            return

        if event == "MOTION_DETECTED":
            self._motion_timer = time.time()
            if not self._power_state:
                print("Motion detected when lights are off!")
                print(f"Turning lights on quickly! LED level = {self._brightness}, Lamp level = {self._lamp_brightness}")
                self.off_because_of_motion = False
                self.fade_leds(1, self._brightness, on_off_event=True)
                self.fade_lamp(1, self._lamp_brightness, on_off_event=True)
                self._power_state = True

        interval = self.delay_levels[self._delay][0]
        motionless_time = time.time() - self._motion_timer
        if motionless_time > 1:
            print(f"interval: {interval}, motionless_time: {motionless_time}")

        if motionless_time > interval:
            if self._power_state:
                print(f"Motion not detected for {interval} seconds")
                print("TURNING LIGHT to 1% AUTOMATICALLY OVER 3 Seconds")
                self.off_because_of_motion = True
                self.fade_leds(3,1, on_off_event=True)
                self.fade_lamp(3,1, on_off_event=True)
                self._power_state = False

    def handle_event(self, event):
        if event is None:
            return

        event = event.strip()

        remote_events = (
                            "POWER_BUTTON",
                            "STOP_BUTTON",
                            "BRIGHTNESS_UP",
                            "LAMP_UP",
                            "BRIGHTNESS_75",
                            "BRIGHTNESS_DOWN",
                            "LAMP_DOWN",
                            "BRIGHTNESS_MIN",
                            "VOLUME_UP",
                            "DELAY_30S",
                            "VOLUME_DOWN",
                            "DELAY_1H",
                            "DELAY_3H",
                            "DELAY_10M",
                            "IR_FAN_STOP",
                            "IR_LIGHT_ON",
                            "IR_LIGHT_OFF",
                            "IR_FAN_LOW",
                            "IR_FAN_MID",
                            "IR_FAN_HIGH",
                            "MUSIC_BUTTON",
                         )

        motion_events = ("MOTION_DETECTED", "MOTIONLESS")

        if event in remote_events:
            print(f"Got remote event: {event}")
            self.handle_remote_event(event)
        elif event in motion_events:
            print(f"Got motion event: {event}")
            self.handle_motion_event(event)
        else:
            if not self.parse_ld2410_info(event) and event != "ACK":
                print(f"Got unknown event {event}")

    def parse_ld2410_info(self, event):
        event = event.strip()
        headers = ("Reading from sensor:", "OK")
        if event in headers:
            return True

        if "Stationary" in event or "Moving" in event:
            print(event)
            return True
        if "Detected" in event:
            return

        return False


    def set_brightness(self, level):
        if level > 100:
            level = 100
        if level < 0:
            level = 0

        self._brightness = level
        self._setPWMBrightness(level)
        print(f"Brightness set to {level}")

    def _setPWMBrightness(self, brightness):
        level = int(brightness * 2.55)
        if brightness == 100:
            level = 255
        if brightness == 0:
            level = 0
        if brightness == 1:
            level = 2

        cmd = f"^SET_PWM {level}$"
        print(cmd)
        sock.sendto(cmd.encode(), (self.PWM_CLIENT_IP, UDP_PORT));
        self.last_pwm_brightness_set = brightness

    def _fade_lamp_thread(self, ftime):
        with lamp_lock:
            time.sleep(ftime+0.1)
        print("Done processing lamp brightness request")


    def fade_lamp(self, ftime, value, on_off_event=False):
        if lamp_lock.locked() and not on_off_event:
            print("Lamp currently processing last request. Returning")
            return False

        while lamp_lock.locked() and on_off_event:
            print("Waiting for last event to end for critical on_off_event request to lamp")
            time.sleep(0.05)

        self.send_to_lamp(int(ftime), int(value))
        print(f"Sent fade request to lamp. Duty = {value}, time = {ftime}")
        if not on_off_event:
            self._lamp_brightness = int(value)
        threading.Thread(target=self._fade_lamp_thread, args=(ftime,)).start()
        return True

    def _fade_leds_thread(self, ftime, value, on_off_event=False):
        start_value = self.last_pwm_brightness_set

        with led_lock:
            if start_value > value:
                r = range(start_value, value - 1, -1)
            else:
                r = range(start_value, value + 1)

            t = ftime/len(r)

            for i in r:
                if lock_fade_request.locked():
                    return
                if on_off_event: #dont set brightness setting when turning on or off
                    self._setPWMBrightness(i)
                else:
                    self.set_brightness(i)
                time.sleep(t)


    def fade_leds(self, ftime, value, on_off_event=False):
        if led_lock.locked() and on_off_event:
            with lock_fade_request:
                while led_lock.locked():
                    time.sleep(.01)
                    print("Waiting for old fade to end early")

        if led_lock.locked():
            print("Led fade is locked")
            return False

        self._fade_thread = threading.Thread(target=self._fade_leds_thread,
                                            args=(ftime, value, on_off_event)).start()
        print("Started fade request thread")
        return True

    def delay_increase(self):
        self._delay += 1

    def delay_decrease(self):
        self._delay -= 1

    def set_delay_level(self, level):
        self._delay = level

    def enable_motion(self):
        self._motion_enabled = True

    def disable_motion(self):
        self._motion_enabled = False

    def load_settings(self):
        print("Loading saved settings")
        if os.path.exists(self.settings_path):
            with open(self.settings_path, 'r') as f:
                saved_settings = json.load(f)


        for key, value in saved_settings.items():
            exec(f"{key} = {value}")
            print(f"Loaded {key} = {value}")

    def play_thread(self, sound):
        with play_lock:
            subprocess.run(['aplay', sound])

    def alert(self, event):
        sound = self.sound_bad_input

        if event == "MOTION_DETECTED" or event == "MOTIONLESS":
            print("No sound for this event")
            return

        elif event == "POWER_BUTTON":
            if self._power_state:
                sound = self.sound_up
            else:
                sound = self.sound_down
        elif event == "STOP_BUTTON":
            if self._motion_enabled:
                sound = self.sound_up
            else:
                sound = self.sound_down
        elif event in ("VOLUME_UP", "BRIGHTNESS_UP"):
            sound = self.sound_up
        elif event in ("VOLUME_DOWN", "BRIGHTNESS_DOWN"):
            sound = self.sound_down
        elif "DELAY" in event:
            sound = self.sound_up
        elif event == "BRIGHTNESS_75":
            if  self._brightness == 75:
                sound = self.sound_bad_input
            elif self._brightness > 75:
                sound = self.sound_down
            elif self._brightness < 75:
                sound = self.sound_up
        elif event == "BRIGHTNESS_MIN":
            if (self._brightness == 1):
                sound = self.sound_bad_input
            else:
                sound = self.sound_up
        elif event == "BRIGHTNESS_100":
            if self._brightness == 100:
                sound = self.sound_bad_input
            elif self._brightness > 100:
                sound = self.sound_down
            elif self._brightness < 100:
                sound = self.sound_up
        elif "LAMP_UP" == event:
            if self._lamp_brightness > 75:
                sound = self.sound_bad_input
            else:
                sound = self.sound_up
        elif "LAMP_DOWN" == event:
            if self._lamp_brightness < 5:
                sound = self.sound_bad_input
            else:
                sound = self.sound_down
        elif event == "IR_FAN_STOP":
            sound = self.sound_down
        elif event == "IR_LIGHT_OFF":
            sound = self.sound_down
        elif event[:3] == "IR_":
            sound = self.sound_up

        if play_lock.locked():
            return

        threading.Thread(target=self.play_thread, args=(sound,), daemon=True).start()


    def save_settings(self):
        with Timer("Save Settings Thread"):
            s = defaultdict(int)

            s['self._brightness'] = self._brightness
            s['self._lamp_brightness'] = self._lamp_brightness
            s['self._delay'] = self._delay
            s['self._motion_enabled'] = self._motion_enabled
            s['self._volume'] = self._volume

            with open(self.settings_path, 'w') as f:
                json.dump(s, f)
            print(json.dumps(s, indent=4))

    def get_data(self, sock):
        try:
            data, addr = sock.recvfrom(32)
            client_name = addr
            event = data.decode('ascii').strip()
            sock.sendto("ACK".encode(), addr);

            global test_mode
            if event == "TEST_MODE 0":
                test_mode = False

            if event == "TEST_MODE 1":
                test_mode = True

            addr=addr[0]
            if test_mode and addr != "192.168.50.39":
                return None


            if addr == self.MOTION_CLIENT_IP:
                addr = "MOTION CLIENT"
            elif addr == self.LAMP_CLIENT_IP:
                addr = "LAMP CLIENT"
            elif addr == self.LD2410_CLIENT_IP:
                addr = "LD2410_CLIENT"

            if "MOTION" in event:
                print(f"Got event : {bcolors.WARNING}{event}{bcolors.ENDC}".ljust(25) + f" from: {bcolors.WARNING}{addr}{bcolors.ENDC}")

            if event == "MOTION_DETECTED" and self._motion_enabled and self.off_because_of_motion and addr == "LD2410_CLIENT":
                print(f"We got motion from LD2410 but are in a timeout state. Only trust the PIR to turn lights on")
                return None

            return event
        except BlockingIOError:
            return None

    def send_to_lamp(self, ftime, level):
        ftime = int(ftime)
        level = int(level)

        cmd = f"LAMPSET {ftime} {level}"

        try:
            sock.sendto(cmd.encode(), (self.LAMP_CLIENT_IP, UDP_PORT));
        except BlockingIOError:
            return None

    def determine_client_addresses(self):
        print("Finding Client IPs...")
        if self.LAMP_CLIENT_IP == "":
            print("Finding LAMP")
            self.LAMP_CLIENT_IP = find_client_ip(self.LAMP_CLIENT_MAC)
        if self.MOTION_CLIENT_IP == "":
            print("Finding PIR Sensor")
            self.MOTION_CLIENT_IP = find_client_ip(self.MOTION_CLIENT_MAC)
        if self.LD2410_CLIENT_IP == "":
            print("Finding LD2410 Sensor")
            self.LD2410_CLIENT_IP = find_client_ip(self.LD2410_CLIENT_MAC)

        print("Found client Ips")
        print(f"LAMP_CLIENT[{self.LAMP_CLIENT_MAC}] : {self.LAMP_CLIENT_IP}")
        print(f"MOTION_CLIENT[{self.MOTION_CLIENT_MAC}] : {self.MOTION_CLIENT_IP}")
        print(f"LD2410_CLIENT[{self.LD2410_CLIENT_MAC}] : {self.LD2410_CLIENT_IP}")


class RF:
    def __init__(self):
        self.rfdevice = None
        rf_pin = 27
        self.rfdevice = RFDevice(rf_pin)
        self.rfdevice.enable_rx()
        self._timestamp = None
        self._code = None
        self.cmd = None

    def get_rf_cmd(self):
        time.sleep(0.01)
        if self.rfdevice.rx_code_timestamp != self._timestamp:
            self._timestamp = self.rfdevice.rx_code_timestamp
            self._code = self.rfdevice.rx_code
            #print(str(self.rfdevice.rx_code) + " [pulselength " + str(self.rfdevice.rx_pulselength) + ", protocol " + str(self.rfdevice.rx_proto) + "]")
            cmd = self.parse_code()
            if cmd is not None:
                self.cmd = cmd
            return cmd
        return None

    def parse_code(self):
        cmd = None
        match self._code:
            case 59137:
                cmd = "POWER_BUTTON"
                print(f"Got remote command: 59137 for {cmd}")
            case 59139:
                cmd = "STOP_BUTTON"
                print(f"Got remote command: 59139 for {cmd}")
            case 59140:
                cmd = "BRIGHTNESS_UP"
                print(f"Got remote command: 59140 for {cmd}")
            case 59141:
                cmd = "LAMP_UP"
                print(f"Got remote command: 59141 for {cmd}")
            case 59142:
                cmd = "BRIGHTNESS_75"
                print(f"Got remote command: 59142 for {cmd}")
            case 59143:
                cmd = "BRIGHTNESS_DOWN"
                print(f"Got remote command: 59143 for {cmd}")
            case 59144:
                cmd = "LAMP_DOWN"
                print(f"Got remote command: 59144 for {cmd}")
            case 59145:
                cmd = "BRIGHTNESS_MIN"
                print(f"Got remote command: 59145 for {cmd}")
            case 59150:
                cmd = "VOLUME_UP"
                print(f"Got remote command: 59150 for {cmd}")
            case 59152:
                cmd = "DELAY_30S"
                print(f"Got remote command: 59152 for {cmd}")
            case 59153:
                cmd = "VOLUME_DOWN"
                print(f"Got remote command: 59153 for {cmd}")
            case 59154:
                cmd = "DELAY_1H"
                print(f"Got remote command: 59154 for {cmd}")
            case 59156:
                cmd = "DELAY_10M"
                print(f"Got remote command: 59156 for {cmd}")

        if cmd is not None:
            print(cmd)

        return cmd



def ping(host):
    with Timer("Ping " + host):
        try:
            socket.inet_aton(host)
        except socket.error:
            return 0

        command = ['ping', '-c', '1', host]
        result = subprocess.run(command, stdout=subprocess.PIPE)
        output = result.stdout.decode('utf8')
        if "Request timed out." in output or "100% packet loss" in output or "Name or service not known" in output:
            return 0
        return 1

def every_10():
    if "start_t" not in globals():
        global start_t
        start_t = time.time()


    if time.time() - start_t > 10:
        del start_t
        return True
    else:
        return False



# Setup Server
MY_IP = "192.168.50.39"
UDP_PORT = 2390
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((MY_IP, UDP_PORT))
sock.setblocking(False)

# Start RF ISR
print("Starting RF Library")
rf = RF()

# Start Light Client Class
print("Starting Light Client Library")
lights = LightClients()

print("Starting Loop")
while True:
    with Timer("Main Loop", 0.05):

        # Parse User Events
        user_cmd = rf.get_rf_cmd()
        lights.handle_event(user_cmd)

        # Parse client events
        motion_data = lights.get_data(sock);
        lights.handle_event(motion_data)
