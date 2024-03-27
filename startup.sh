sudo pigpiod

stty -F /dev/ttyACM0 115200

while [ 1 ]
do
    sleep 10
    led_pid=$(pgrep -a -f "python3.*leds.py" | awk '{print $1}')

    # LED LOGIC KEEP ALIVE
    echo $led_pid
    if ps -p $led_pid > /dev/null
    then
        echo "led.py is running"
    else
        echo "led.py not running. Starting"
        python3 /root/EllieD/leds.py &
    fi

    ls -1rt -d -1 /root/EllieD/logs/* | head -n -10 | xargs -d '\n' rm -f --

done
