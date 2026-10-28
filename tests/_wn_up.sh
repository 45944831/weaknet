#!/system/bin/sh
# 用法: wn_up.sh <host> <port> <body_bytes>
# 上行测速：从发出请求到「服务端返回首个响应字节」的墙钟。
#
# 为什么不能只靠 nc 退出判定：nc 在 stdin EOF 时立即关 socket，内核缓冲里没发完
# 的数据会被丢弃（实测 1MB/0.03s 假象）。所以用 sleep 300 把 stdin 撑住，
# 再用轮询输出文件的方式等服务端真正的响应。
#
# 为什么 body 用 head -c 而不是 dd bs=4096 count=$((B/4096))：B<4096 时 count=0，
# 会发出空 body，服务端等不满 Content-Length 永不响应，探针自己把自己挂死。
H=$1; P=$2; B=$3
O=/data/local/tmp/wn_up
rm -f $O
now() { read U _ < /proc/uptime; echo "$U" | awk -F. '{printf "%d", $1*1000 + substr($2 "00", 1, 3)}'; }
S=$(now)
# 撑 stdin 的 sleep 带本脚本 PID，pkill -f 才能精确只杀自己那个：裸 sleep 300
# 在并发探针之间会互相误杀（对端提前 EOF），而完全不 pkill 又会让 sleep 活
# 300s 拖着 adb shell 一起等，探针算完了却卡到超时。
SL="sleep 300.$$"
{ printf 'POST http://127.0.0.1:9130/ HTTP/1.1\r\nHost: x\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "$B"
  head -c "$B" /dev/zero
  $SL
} | timeout 240 nc $H $P > $O 2>/dev/null &
BG=$!
i=0; SZ=0
while [ $i -lt 2200 ]; do
  sleep 0.1
  SZ=$(wc -c < $O 2>/dev/null)
  [ -z "$SZ" ] && SZ=0
  [ "$SZ" -gt 0 ] && break
  i=$((i+1))
done
E=$(now)
kill $BG 2>/dev/null
pkill -f "$SL" 2>/dev/null
echo "UP $SZ $((E-S))"
