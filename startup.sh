sudo pigpiod

led_pid = 0
while [ 1 ]
do
    sleep 10

    # LED LOGIC KEEP ALIVE
    if ps -p $led_pid > /dev/null
    then
        #pid is running. Do nothing
        :
    else
        python3 /root/EllieD/leds.py &
        led_pid=$!
    fi
done
