from machine import Pin
from machine import ADC
from machine import Timer
from machine import reset
from machine import WDT
from network import WLAN
from network import STA_IF
from math import log
from time import sleep
from json import dumps, loads, dump, load

#import libs
from libs.power_monitoring import CurrentRead, VoltageRead
from libs.temperature import NTC_Temperature
from libs.simple import MQTTClient
from libs.uping import ping
import ntptime
from gc import enable, collect
from libs.config_proc import *

#gc enable
enable()
collect()

# remember to enable timers
wdt = WDT(timeout=hppc_config['LOG_INTERVAL'] + 30000)  # set timeout to 2 seconds 

###### PIN ASSIGNMENTS ######
# Debug LED
debug_led = Pin(21,Pin.OUT, value = 0)
debug_led_state = False

# 12 &24V Load Switches
_12v = Pin(12,Pin.OUT)            # Active Low
_24v_poe = Pin(11,Pin.OUT)  # Active Low

# ADC pin setup
# Batttery Current
battery_current_pin = ADC(Pin(6))        
battery_current_pin.atten(ADC.ATTN_11DB)
battery_current_pin.width(ADC.WIDTH_13BIT)
# Battery Voltage
battery_voltage_pin = ADC(Pin(5))        
battery_voltage_pin.atten(ADC.ATTN_11DB)
battery_voltage_pin.width(ADC.WIDTH_13BIT)
# Panel Current
panel_current_pin = ADC(Pin(2))        
panel_current_pin.atten(ADC.ATTN_11DB)
panel_current_pin.width(ADC.WIDTH_13BIT)
# Panel Voltage
panel_voltage_pin = ADC(Pin(1))        
panel_voltage_pin.atten(ADC.ATTN_11DB)
panel_voltage_pin.width(ADC.WIDTH_13BIT)
# Load Current
load_current_pin = ADC(Pin(4))        
load_current_pin.atten(ADC.ATTN_11DB)
load_current_pin.width(ADC.WIDTH_13BIT)
# Load Voltage
load_voltage_pin = ADC(Pin(3))        
load_voltage_pin.atten(ADC.ATTN_11DB)
load_voltage_pin.width(ADC.WIDTH_13BIT)
# Temperature
temperature_pin = ADC(Pin(7))        
temperature_pin.atten(ADC.ATTN_11DB)
temperature_pin.width(ADC.WIDTH_13BIT)

# create hw timers
timer_one = Timer(0)
timer_two = Timer(1)

# this is the dictionary used to store the state of the HomePoynt Power Controller as well as its peripherals
DATA = {
    'id'                 : hppc_config['ID'],
    'site_id'		     : hppc_config['SITE_ID'][0],
    'scc_load_voltage'   : 1, # scc_load_voltage
    'scc_load_current'   : 1, # scc_load_current
    'battery_voltage'    : 1, # battery_voltage
    'battery_current'    : 1, # battery_current
    'solar_voltage'      : 1, # solar_voltage
    'solar_current'      : 1, # solar_current
    'temperature'        : 1,
    'connected_to_wifi'  : False,
    'battery_connected'  : False,
    'panel_connected'    : False,
    'error_message_1'    : None,
    'error_message_2'    : None,
    'error_message_3'    : None,
    'error_message_4'    : None,
    'time'               : None
    }

# This should be sent to cloud dashboard for remote control
CONFIG = {
    'ID'                    : hppc_config['ID'], 
    'WIFI_CREDENTIALS'      : hppc_config['WIFI_CREDENTIALS'],
    'MQTT_UN'               : hppc_config['MQTT']['CREDENTIALS'][0],
    'MQTT_PW'               : hppc_config['MQTT']['CREDENTIALS'][1],
    'MQTT_SERVER'           : hppc_config['MQTT']['MQTT_SERVER'][0],
    'MQTT_DATA_TOPIC'       : hppc_config['MQTT']['MQTT_DATA_TOPIC'][0],
    'MQTT_CONTROL_TOPIC'    : hppc_config['MQTT']['MQTT_CONTROL_TOPIC'][0],
    'CHARGER_PROFILE'       : hppc_config['CHARGER_PROFILE'],
    'LOG_INTERVAL'          : hppc_config['LOG_INTERVAL'],
    'LOAD_RESET_INTERVAL'   : hppc_config['LOAD_RESET_INTERVAL'],
    '12V_LOAD_ON'           : hppc_config['12V_LOAD_ON'], 
    'POE_LOAD_ON'           : hppc_config['POE_LOAD_ON'], 
    'TIMEZONE'              : hppc_config['TIMEZONE'], # not used at yet
    'CONFIG_FILE'           : hppc_config['CONFIG_FILE']
    }

#print(CONFIG)
#sleep(100)

def log_state():
    global DATA, CONFIG, hppc_config
    with open('/log.txt', 'w') as f:
        f.write(dumps(DATA))
        f.write('\n')
        f.write(dumps(CONFIG))
        f.write('\n')
        f.write(dumps(hppc_config))
        f.write('\n')
        f.write(str(test_count))
    f.close()

def enable_24v_poe(state):
    ''' This function used to toggle the 24V HomePoynt load'''
    global _24v_poe, EN_24V_POE
    if type(state) != bool:
        raise Exception('Must be boolean state: True or False')
    if state == True:
        _24v_poe.value(1)
        EN_24V_POE = True
    if state == False:
        _24v_poe.value(0)
        EN_24V_POE = False
    return EN_24V_POE


def enable_12v(state):
    ''' This function used to toggle the 12V load'''
    global _12v, EN_12V
    if type(state) != bool:
        raise Exception('Must be boolean state: True or False')
    if state == True:
        _12v.value(1)
        EN_12V = True
    if state == False:
        _12v.value(0)
        EN_12V = False
    return EN_12V

def blink_debug_led(times):
    for i in range(times):
        debug_led.value(1)
        sleep(0.5)
        debug_led.value(0)
        sleep(0.5)

def toggle_debug_led():
    global debug_led_state
    
    if debug_led_state:
        debug_led.value(1)
        debug_led_state = False
    else:
        debug_led.value(0)
        debug_led_state = True
    
def setup():
    '''This function sets up the power monitor states, initial load states and verifies internet connection '''
    global hppc_config, DATA, EN_24V_POE, EN_12V, battery_voltage, battery_current, panel_voltage, panel_current, load_voltage, load_current, thermistor
    
    # Setup curremt, voltage temperature measurement classes
    # Current Classes
    battery_current = CurrentRead(battery_current_pin, 1.660, -0.001, 3.332, "BATTERY_CURRENT", 88700, 20000) # 2.66735
    panel_current = CurrentRead(panel_current_pin, 1.671, -0.007,  3.332, "PANEL_CURRENT", 88700, 20000)
    load_current = CurrentRead(load_current_pin, 1.688, -0.008, 3.332, "LOAD_CURRENT", 88700, 20000) #2.66735

    # Voltage Classes
    battery_voltage = VoltageRead(battery_voltage_pin, 0.001, 0, "BATTERY_VOLTAGE", 88700, 1000000)
    panel_voltage = VoltageRead(panel_voltage_pin, 0, 0, "PANEL_VOLTAGE", 178000, 1000000)
    load_voltage = VoltageRead(load_voltage_pin, 0, 0.1, "LOAD_VOLTAGE", 88700, 1000000)

    # Temperature
    thermistor = NTC_Temperature(temperature_pin,
                                 set_point = 0.595, Bval = 470000,
                                 A1 = 3.354016E-03,
                                 B1 = 2.264097E-04,
                                 C1 = 3.278184E-06,
                                 D1 = 1.097628E-07,
                                 temp_offset = 1.35)
    
    # Set PoE and 12V state
    enable_24v_poe(CONFIG['POE_LOAD_ON'])
    enable_12v(CONFIG['12V_LOAD_ON'])
    
    # Verify internet by pinging google
    while 1:
        if do_connect():
            if ping_google():
                DATA['connected_to_wifi'] = True
                print('Internet connection established...')
                break
            else:
                DATA['connected_to_wifi'] = False
                print('Internet connection could not be established. Retrying...')
    # Setup MQTT client
    mqtt_setup(CONFIG['ID'], CONFIG['MQTT_SERVER'], CONFIG['MQTT_UN'], CONFIG['MQTT_PW'] ,CONFIG['LOG_INTERVAL']/1000 + 60)
    subscribe_to_control(client, topic = CONFIG['MQTT_CONTROL_TOPIC'])
    
    return True

def do_connect():
    '''Establishs wifi connection'''
    global hppc_config, CONFIG, DATA
    wlan = WLAN(STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print("WiFi already connected...")

    while not wlan.isconnected():
        for site_name, credentials in CONFIG['WIFI_CREDENTIALS'].items():
            print('Attempting to connect using WiFi Profile: {} '.format(site_name))
            ssid = credentials[0]
            pwd = credentials[1]
            
            try:
                wlan.connect(ssid, pwd)
                sleep(5)
                if wlan.isconnected():
                    print("WiFi connected...")
                    break
                else:
                    print("Attempt failed...\n")
                    sleep(1)
            except:
                wlan.disconnect()
                print("Attempt failed...\n")
                sleep(1)
                pass  
    
    print('network config:', wlan.ifconfig())        
        
    DATA['connected_to_wifi'] = True
     
    # Update time with ntp server
    while 1:
        try:
            ntptime.settime()
        except:
            print('syncing time...')
        else:
            print("time synced...")
            break
    
    return True
    
def ping_google():
    '''use uPing library to ping google'''
    x = ping('google.com', count=4, timeout=5000, interval=10, quiet=True, size=64)
    if x == (4, 4):
        return True
    else:
        return False
 
def verify_internet_connection():
    '''function to set wifi connection flags'''
    global DATA, DEBUG_STATE
    if ping_google():
        DATA['connected_to_wifi'] = True
        return True
    else:
        DATA['connected_to_wifi'] = False
        return False

def get_panel_voltage(panel_voltage, battery_voltage, offset = 0.4): # instantiate VoltageRead class for panel
    """
    Panel voltage is calulated differently due to hw mods.
    Use this function speciifcally for measuring the Panel Voltage
    """
    battery_v = battery_voltage.get_voltage()
    
    panel_v_adc = panel_voltage.get_adc_voltage()
    panel_v_adc = 3.3 - panel_v_adc  # correct panel adc voltage to account for 3.3V ref instead of gnd
    Vpanel = ((panel_v_adc/180000)*1180000) - 3.33
    return battery_v + Vpanel + offset

def fetch_data(charger = CONFIG['CHARGER_PROFILE']): 
    '''Function to update voltage, current, and panel/battery connection states\
used for two profiles only at the moment'''
    global DATA, CONFIG, state, test_count

    DATA['scc_load_voltage'] = load_voltage.get_voltage()
    DATA['scc_load_current'] = load_current.get_current()
    DATA['battery_voltage'] = battery_voltage.get_voltage()
    DATA['battery_current'] = battery_current.get_current()
    DATA['solar_voltage'] = get_panel_voltage(panel_voltage, battery_voltage, offset=0) #panel_voltage.get_voltage()
    DATA['solar_current'] = panel_current.get_current()

    # check if 24V battery is connected 
    if DATA['battery_voltage'] < 20:
        DATA['battery_connected'] = False
    if DATA['battery_voltage'] > 20:
        DATA['battery_connected'] = True
   
    if DATA['solar_voltage'] < DATA['battery_voltage']:
        DATA['panel_connected'] = False
    else:
        DATA['panel_connected'] = True
   
    DATA['temperature'] =  thermistor.get_temperature()
    DATA['connected_to_wifi'] = verify_internet_connection()
    
    wdt.feed() # this one
    test_count += 1
    print("wdt fed...{}".format(test_count))
    
    return True

def safety_check():
    '''checks that voltages and currents are not above the limits of the devices and sets the appropriat condition flags'''
    global DATA, ERROR_STATES, hppc_config,  EN_12V, EN_24V_POE

    # Load protection against input voltage which the 12V reg cannot handle
    if DATA['scc_load_voltage'] > hppc_config['SCC_MAX_VOLTAGE']:
        ERROR_STATES['scc_over_voltage'] = True
        DATA['error_message_1'] = "WARNING: SCC LOAD VOLTAGE IS ABOVE MAX VOLTAGE RATING. POWER CONTROLLER LOADS TURNED OFF"
       
    # Load protection against low voltage (This is to ensure that the HomePoynt Load Is powered by a minimum voltage)    
    if DATA['scc_load_voltage'] < hppc_config['SCC_MIN_VOLTAGE']:
        ERROR_STATES['scc_under_voltage'] = True
        DATA['error_message_2'] = "WARNING: SCC LOAD VOLTAGE IS BELOW MIN VOLTAGE RATING. POWER CONTROLLER LOADS TURNED OFF"
        
    # If "scc_load_voltage" is within correct range, set/reset relevant flags
    if  DATA['scc_load_voltage'] < hppc_config['SCC_MAX_VOLTAGE'] and DATA['scc_load_voltage'] > hppc_config['SCC_MIN_VOLTAGE']:
        ERROR_STATES['scc_under_voltage'] = False
        ERROR_STATES['scc_over_voltage'] = False
        DATA['error_message_1'] = None
        DATA['error_message_2'] = None
    
    # Load Protection against too much current draw which could reach regulator limits
    if DATA['scc_load_current'] > hppc_config['SCC_MAX_CURRENT']:
        ERROR_STATES['scc_over_current'] = True        
        DATA['error_message_3'] = "WARNING: SCC LOAD IS OVER CURRENT RATING. POWER CONTROLLER LOADS TURNED OFF"
      
    # If  "scc_load_current" is within the correct range, set/reset relevant flags
    if DATA['scc_load_current'] < hppc_config['SCC_MAX_CURRENT']:
        ERROR_STATES['scc_over_current'] = False
        DATA['error_message_3'] = None
    
    # Battery Protection: Battery max voltage cannot exceed the max voltage at which the battery can be charged at (Dependant on battery and charge controller charging stage settings)
    if DATA['battery_voltage'] > hppc_config['BATT_MAX_VOLTAGE']:
        ERROR_STATES['batt_charging_voltage_too_high'] = True
        DATA['error_message_4'] = "WARNING: BATTERY CHARGING VOLTAGE IS TOO HIGH. POWER CONTROLLER LOADS TURNED OFF"
        
    if DATA['battery_voltage'] < hppc_config['BATT_MAX_VOLTAGE']:
        ERROR_STATES['batt_charging_voltage_too_high'] = False
        DATA['error_message_4'] = None
    # Panel Protection: No protection, can only say if the panel is connected or not  
    
    # Checks for any error states which are true
    safty_state = (ERROR_STATES['scc_over_current'] or ERROR_STATES['scc_under_voltage']) or (ERROR_STATES['scc_over_voltage'] or ERROR_STATES['batt_charging_voltage_too_high'])
    
    # Switched all loads off based on error state estimate
    if safty_state:
        EN_12V = False
        EN_24V_POE = False
    
    update_board_states()

# add code to request error state dictionary (will help with remote debugging)
# error state dictionary
ERROR_STATES = {
    'scc_over_voltage' : False,
    'scc_under_voltage' : False,
    'scc_over_current' : False,
    'batt_charging_voltage_too_high' : False,
    }

def printDATA():
    '''Used to verify DATA dictionary, which gets sent to the MQTT server'''
    global DATA
    print("\033[4m" + "DATA DICTIONARY" + "\033[0m")
    print('scc_load_voltage : {:>36.7} V'.format(DATA['scc_load_voltage']))
    print('scc_load_current : {:>40.7} A'.format(DATA['scc_load_current']))
    
    print('battery_connected : {!r:>36}'.format(DATA['battery_connected']))
    print('battery_voltage : {:>27.7} V'.format(DATA['battery_voltage']))
    print('battery_current : {:>31.7} A'.format(DATA['battery_current']))
    
    print('panel_connected : {!r:>39}'.format(DATA['panel_connected']))
    print('solar_voltage : {:>30.7} V'.format(DATA['solar_voltage']))
    print('solar_current : {:>34.7} A'.format(DATA['solar_current']))
    
    print('temperature : {:>49.7} {}C'.format(DATA['temperature'], u"\u00b0"))
    print('connected_to_wifi : {!r:>36}'.format(DATA['connected_to_wifi']))
    
    
    print('error_message_1 : {!r:>38}'.format(DATA['error_message_1']))
    print('error_message_2 : {!r:>38}'.format(DATA['error_message_2']))
    print('error_message_3 : {!r:>38}'.format(DATA['error_message_3']))
    print('error_message_4 : {!r:>38}'.format(DATA['error_message_4']))
    print('time : {!r:>64}'.format(DATA['time']))

def printERROR_STATES():
    global ERROR_STATES
    print("\n\033[4m" + "ERROR STATES DICTIONARY" + "\033[0m")
    print('scc_over_voltage : {!r:>39}'.format(ERROR_STATES['scc_over_voltage']))
    print('scc_under_voltage : {!r:>36}'.format(ERROR_STATES['scc_under_voltage']))
    print('scc_over_current : {!r:>39}'.format(ERROR_STATES['scc_over_current']))
    print('batt_charging_voltage_too_high : {!r:>8}'.format(ERROR_STATES['batt_charging_voltage_too_high']))

def printCONFIG():
    global CONFIG
    print("\n\033[4m" + "CONFIG DICTIONARY" + "\033[0m")
    print('ID : {!r:>70}'.format(CONFIG['ID']))
    print('WIFI_SSID : {!r:>60}'.format(CONFIG['WIFI_SSID']))
    print('WIFI_PWD : {!r:>54}'.format(CONFIG['WIFI_PWD']))
    print('MQTT_SERVER : {!r:>41}'.format(CONFIG['MQTT_SERVER']))
    print('MQTT_DATA_TOPIC : {!r:>49}'.format(CONFIG['MQTT_DATA_TOPIC']))
    print('MQTT_CONTROL_TOPIC : {!r:>49}'.format(CONFIG['MQTT_CONTROL_TOPIC']))
    print('CHARGER_PROFILE : {!r:>32}'.format(CONFIG['CHARGER_PROFILE']))
    print('LOG_INTERVAL : {!r:>42}'.format(CONFIG['LOG_INTERVAL']))
    print('TIMEZONE : {!r:>52}'.format(CONFIG['TIMEZONE']))
    print('LOAD_RESET_INTERVAL : {!r:>25}'.format(CONFIG['LOAD_RESET_INTERVAL']))
    print('12V_LOAD_ON : {!r:>43}'.format(CONFIG['12V_LOAD_ON']))
    print('POE_LOAD_ON : {!r:>42}\n'.format(CONFIG['POE_LOAD_ON']))    

# VERIFY THE FLAG STATE CHANGE 
def update_board_states():
    '''Used to update the states on the board'''
    global DATA, EN_24V_POE, EN_12V
    enable_24v_poe(EN_24V_POE)
    enable_12v(EN_12V)
    
def getTime():
    global CONFIG
    _year, _month, _day, _hour, _min, _sec, _, _ = time.localtime() #time.gmtime() #
    # Checks that timezone is correct.
#     current_timezone = CONFIG['TIMEZONE']
#     if current_timezone > -12 and current_timezone < 13:
#         time_zone = current_timezone
#     else:
#         time_zone = 0
    return "{}_{:02d}_{:02d}_{:02d}_{:02d}_{:02d}".format(_year, _month, _day, _hour + CONFIG['TIMEZONE'], _min, _sec)

# TIMER CALLBACK FUNCTIONS
def send_data(timer_one):
    '''used to publish data to the MQTT server'''
    global DATA, CONFIG, client
    DATA['time'] = getTime()
    
    while 1:
        if DATA['connected_to_wifi']:
            log_state()
            client.publish(b"{}".format(CONFIG['MQTT_DATA_TOPIC']), b"{}".format(dumps(DATA)), qos=1)
            break
        else:
            # gc collect
            collect()
            do_connect()
            mqtt_setup(CONFIG['ID'], CONFIG['MQTT_SERVER'], CONFIG['MQTT_UN'], CONFIG['MQTT_PW'], CONFIG['LOG_INTERVAL']/1000 + 60)
            subscribe_to_control(client, topic = CONFIG['MQTT_CONTROL_TOPIC'])    

def authenticate_control_config(CONFIG, control_config):
    """
    Validates incoming dictionary from broker to ensure that the correct device is being updated.
    Form of minimal security, using device ID.
    """
    if 'ID' in control_config.keys():
        if control_config['ID'] == CONFIG['ID']:
            sleep(2) # required for mqtt function to complete, as a blocking library is used 
            return 1
        else:
            print("Invalid ID") # publish this to control
            client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"Invalid ID" : "Please provide correct ID."})), qos=1)
            sleep(2) # required for mqtt function to complete, as a blocking library is used 
            return 0
       
def control_callback(topic, msg):
    collect()
    global CONFIG, client, state
    # decode mqtt mesaage into utf-8
    control_config = msg.decode('utf-8') # decodes message from broker as text 
    control_config = loads(control_config) # converts broker message to a dictionary
    
    #print(control_config)
    #print("\n")
    
    if authenticate_control_config(CONFIG, control_config): # Enforce that correct ID is in the command coming from the broker
        for control_key, control_value in control_config.items(): #cycle through control config
            if control_key == 'COMMAND':
                if control_value == 'RESET':
                    collect()
                    client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"RESET" : "DEVICE RESET"})), qos=1)
                    print("RESET command initiated")
                    timer_one.deinit()
                    timer_two.deinit()
                    client.disconnect()
                    sleep(5)
                    reset()
                    
                elif control_value == 'SHOW_WIFI_CREDENTIALS':
                    collect()
                    print("SHOW_WIFI_CREDENTIALS command initiated")
                    client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"SHOW_WIFI_CREDENTIALS" : CONFIG['WIFI_CREDENTIALS']})), qos=1)
                    sleep(2) # required for mqtt function to complete, as a blocking library is used
    
                elif control_value == 'SHOW_CONFIG_FILE':
                    collect()
                    print("SHOW_CONFIG_FILE command initiated")
                    client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"SHOW_CONFIG_FILE" : load_config()})), qos=1) # change to get_wifi_credentials
                    collect()
                    sleep(5) # required for mqtt function to complete, as a blocking library is used

                else:
                    print("Invalid Command")
                    collect()
                    return 1

            elif control_key == 'WIFI_CREDENTIALS': # update_config_file
                collect()
                print("WIFI_CREDENTIALS_UPDATE command initiated")
                wifi_message = update_config_file(control_key, control_value)
                client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"WIFI_CREDENTIALS_UPDATE" : wifi_message})), qos=1)
                sleep(2)

            elif control_key == 'MQTT':
                collect()
                print("MQTT_CONFIG update initiated")
                mqtt_message = update_config_file(control_key, control_value)
                client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"MQTT_CONFIG" : mqtt_message})), qos=1)
                sleep(2)

            elif control_key == 'SITE_ID':
                print("SITE_ID update initiated")
                site_id_update_message = update_config_file(control_key, control_value)
                client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"SITE_ID_UPDATE" : site_id_update_message})), qos=1)
                sleep(2)
                
            elif control_key == 'ID': # fix this
                collect()
                print( CONFIG['ID'])
            
            else:
                state = 'get_voltages_and_currents'
                print("Invalid key used for update")
    else: # authentication failed or message published from device being caught
        commands =  ['RESET', 'SHOW_WIFI_CREDENTIALS', 'SHOW_CONFIG_FILE',  'WIFI_CREDENTIALS_UPDATE', 'MQTT_CONFIG', 'SITE_ID_UPDATE']
        collect()
        if list(control_config.keys())[0] in commands:
            print("{} command successful".format(list(control_config.keys())[0]))
        else:
            print("Unknown Publish to Control\n")
            print(control_config)
        return 0 
             
def mqtt_setup(_id, _server, _un, _pw, _keepalivetime):
    global client
    _keepalivetime = _keepalivetime/1000
    if _keepalivetime < 60:
        _keepalivetime = 60
        
    client = MQTTClient( _id, _server,  user=_un, password=_pw, keepalive=0) # assign double the log_interval time so that mqtt server doesn't close the connection between publishes
    client.connect()
    client.publish(b"{}".format(CONFIG['MQTT_CONTROL_TOPIC']), b"{}".format(dumps({"HPPC" : "DEVICE RUNNING"})), qos=1)
    
def subscribe_to_control(mqtt_client, topic = CONFIG['MQTT_CONTROL_TOPIC']):
    """This function subscribes to the control topic and sets the appropriate callback function"""
    mqtt_client.set_callback(control_callback)
    mqtt_client.subscribe(topic, qos=1)

def load_reset_interval(timer_two):
    global CONFIG, EN_12V, EN_24V_POE
    # switch 12V Load on (or to config state)
    EN_12V = CONFIG['12V_LOAD_ON']
    # switch PoE Load on (or to config state)
    EN_24V_POE = CONFIG['POE_LOAD_ON']

# Used to verify that execution has reached the beginning of the state control loop 
blink_debug_led(3)
DEBUG_STATE = False 
test_count = 0

#set initial state
states = ['setup', 'get_voltages_and_currents', 'send_data', 'debug']
state = states[0]

try:
    # state control loop
    while 1:
        # gc collect
        collect()
        #await asyncio.sleep_ms(100)
        # Set the HomePoynt Power Controller up
        if state == 'setup':
            while 1:
                if setup():
                    state = 'get_voltages_and_currents'
                    # Initialise Timers
                    timer_one.init(period = CONFIG['LOG_INTERVAL'], callback=send_data)
                    timer_two.init(period = CONFIG['LOAD_RESET_INTERVAL'], callback=load_reset_interval)
                    break
                else:
                    pass
                print("Setup Failed")
                
        # Fetches the states from the Homepoynt Power Controller
        if state == 'get_voltages_and_currents':
            client.check_msg()
            while 1:
                if fetch_data(): #charger = "epever_Tracer2210AN" # defaults to gamistar
                    state = 'send_data'
                    break
                else:
                    pass
                    print("get_voltages_and_currents state failed")
         
        # Sends the states to the MQTT server 
        if state == 'send_data':
            while 1:
                safety_check()
                if DEBUG_STATE:
                    state = 'debug'
                    break
                else:
                    state = 'get_voltages_and_currents'
                    break
        
        if state == 'debug':
            printDATA()
            printERROR_STATES()
            printCONFIG()
            state = 'get_voltages_and_currents'    
            
        if state not in states:
            # The code should never get here, if it does you have earned a spoiler
            DATA['scc_load_voltage'] = None
            DATA['scc_load_current'] = None
            DATA['battery_voltage'] = None
            DATA['battery_current'] =  None
            DATA['solar_voltage'] = None
            DATA['solar_current'] = None
            DATA['temperature'] = None
            DATA['connected_to_wifi'] = None
            DATA['battery_connected'] = None
            DATA['panel_connected'] = None
            DATA['error_message_1'] = "OZARK"
            DATA['error_message_2'] = "WENDY KILLS HER BROTHER"
            DATA['error_message_3'] = "RUTH KILLS NEVARO'S NEPHEW"
            printDATA()
            break
        else:
            #print("Current state var is: {}".format(state))
            toggle_debug_led()
            
except KeyboardInterrupt:
    timer_one.deinit()
    timer_two.deinit()
    client.disconnect()
    print("CTRL+C PRESSED...script exited")
    



