import time
import wifi
import socketpool
import board
import neopixel
import ipaddress
from adafruit_httpserver import Server, Request, Response, POST, Redirect

# --- LED Strip Setup ---
PIXEL_PIN = board.GP0
NUM_PIXELS = 300
pixels = neopixel.NeoPixel(PIXEL_PIN, NUM_PIXELS, brightness=0.5, auto_write=False)
auto_color_enabled = False
auto_color_position = 0
last_auto_color_update = 0

# --- Wi-Fi Access Point Setup ---
print("Configuring Access Point...")
wifi.radio.start_ap(ssid="PicoW-LED-Control", password="password123")

ap_ip = ipaddress.IPv4Address("192.168.4.1")
ap_netmask = ipaddress.IPv4Address("255.255.255.0")
ap_gateway = ipaddress.IPv4Address("192.168.4.1")

wifi.radio.set_ipv4_address_ap(ipv4=ap_ip, netmask=ap_netmask, gateway=ap_gateway)

print(f"AP Active! Connect your phone to: PicoW-LED-Control")
print(f"Server Web Address: http://{wifi.radio.ipv4_address_ap}:80")

# Initialize Server
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, "/index.html", debug=True)


# --- LED Strip Actions ---
def clear_strip():
    global auto_color_enabled
    auto_color_enabled = False
    pixels.fill((0, 0, 0))
    pixels.show()


pixels[0] = (32, 0, 0)
pixels.show()
time.sleep(0.5)
clear_strip()


def set_solid_color(r, g, b):
    global auto_color_enabled
    auto_color_enabled = False
    pixels.fill((r, g, b))
    pixels.show()


def wheel(pos):
    if pos < 0 or pos > 255:
        return (0, 0, 0)
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    pos -= 170
    return (pos * 3, 0, 255 - pos * 3)


def rainbow_cycle():
    global auto_color_enabled
    auto_color_enabled = False
    for i in range(NUM_PIXELS):
        pixel_index = (i * 256 // NUM_PIXELS) & 255
        pixels[i] = wheel(pixel_index)
    pixels.show()


def start_auto_color():
    global auto_color_enabled, last_auto_color_update
    auto_color_enabled = True
    last_auto_color_update = 0


def update_auto_color():
    global auto_color_position, last_auto_color_update
    now = time.monotonic()
    if auto_color_enabled and now - last_auto_color_update >= 0.03:
        auto_color_position = (auto_color_position + 1) & 255
        pixels.fill(wheel(auto_color_position))
        pixels.show()
        last_auto_color_update = now


# --- HTML Interface ---
def get_html_page():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Pico W Strip Controller</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 30px; background-color: #121212; color: white; }}
            .btn {{ display: inline-block; width: 80%; max-width: 300px; padding: 15px; font-size: 18px; color: white; border: none; border-radius: 8px; cursor: pointer; margin: 10px 0; text-transform: uppercase; font-weight: bold; }}
            .red {{ background-color: #e53935; }}
            .green {{ background-color: #43a047; }}
            .blue {{ background-color: #1e88e5; }}
            .rainbow {{ background-image: linear-gradient(to right, red, orange, yellow, green, blue, indigo, violet); }}
            .auto {{ background-color: #8e24aa; }}
            .off {{ background-color: #555555; }}
            h2 {{ color: #00e676; }}
        </style>
    </head>
    <body>
        <h2>NeoPixel Control Panel</h2>
        <p>{NUM_PIXELS} LEDs on GP0</p>
        <hr style="width: 80%; border-color: #333;">
        
        <form method="POST" action="/action?type=red"><button type="submit" class="btn red">Solid Red</button></form>
        <form method="POST" action="/action?type=green"><button type="submit" class="btn green">Solid Green</button></form>
        <form method="POST" action="/action?type=blue"><button type="submit" class="btn blue">Solid Blue</button></form>
        <form method="POST" action="/action?type=rainbow"><button type="submit" class="btn rainbow">Rainbow Fill</button></form>
        <form method="POST" action="/action?type=auto"><button type="submit" class="btn auto">Auto Color</button></form>
        <form method="POST" action="/action?type=off"><button type="submit" class="btn off">Turn Off</button></form>
    </body>
    </html>
    """


# --- Server Routes ---
@server.route("/")
def base(request: Request):
    return Response(request, get_html_page(), content_type="text/html")


@server.route("/action", POST)
def handle_action(request: Request):
    query_params = request.query_params
    action_type = query_params.get("type")

    if action_type == "red":
        set_solid_color(255, 0, 0)
    elif action_type == "green":
        set_solid_color(0, 255, 0)
    elif action_type == "blue":
        set_solid_color(0, 0, 255)
    elif action_type == "rainbow":
        rainbow_cycle()
    elif action_type == "auto":
        start_auto_color()
    elif action_type == "off":
        clear_strip()

    # Fixed redirect using the library's official Redirect class
    return Redirect(request, "/")


# Start server explicitly bound to our static AP IP address string
server.start(str(wifi.radio.ipv4_address_ap), port=80)

while True:
    server.poll()
    update_auto_color()
