sleep 5
echo "TEST_MODE 1" | socat - udp:192.168.50.39:2390,sp=2391
sleep 1
echo "MOTIONLESS" | socat - udp:192.168.50.39:2390,sp=2391
sleep 30
echo "MOTIONLESS" | socat - udp:192.168.50.39:2390,sp=2391
sleep 5
echo "TEST: Lights should be dimmed down"
sleep 5
echo "MOTION_DETECTED" | socat - udp:192.168.50.39:2390,sp=2391
sleep 1
echo "MOTION_DETECTED" | socat - udp:192.168.50.39:2390,sp=2391
sleep 1
echo "MOTION_DETECTED" | socat - udp:192.168.50.39:2390,sp=2391
sleep 1
echo "TEST: Lights should be on"
echo "TEST_MODE 0" | socat - udp:192.168.50.39:2390,sp=2391
