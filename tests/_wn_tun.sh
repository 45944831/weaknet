#!/system/bin/sh
# 用法: wn_tun.sh <host> <port> [nbytes] [tag]
# tag 用于隔离输出文件：并发跑多个探针时每个必须写自己的文件，
# 否则共用一个文件会让探针读到别人的字节而提前退出（见 _wn_par.sh 说明）。
#
# 计时口径：从发起连接到「收满预期字节」的墙钟，不含任何人为 sleep 等待。
# 为什么不用 wc -c 收尾：toybox nc 在 stdin EOF 时立即退出，printf|nc 会丢掉
# 尚未读到的响应（实测 BYTES 0 / 20ms）。改成后台跑 nc + 轮询输出文件大小，
# 服务端 9126 的响应头固定 37+len(N) 字节，故 EXPECT 可精确算出来。
H=$1; P=$2; N=${3:-0}; TAG=${4:-0}
O=/data/local/tmp/wn_out_${TAG}.bin
rm -f $O
HDR=$((37 + ${#N}))
EXP=$((HDR + N))
now() { read U _ < /proc/uptime; echo "$U" | awk -F. '{printf "%d", $1*1000 + substr($2 "00", 1, 3)}'; }
if [ "$N" -gt 0 ]; then LIM=1400; else LIM=200; fi
S=$(now)
# 撑住 nc 的 stdin 用「带 PID 的 sleep」，而不是裸 sleep 300：
#   * 裸 sleep 只能靠 pkill -f 'sleep 300' 收尾，并发跑多个探针时先结束的那个
#     会把别人的 sleep 一起杀掉，对端 nc 提前 EOF、连接被掐，测量全乱
#     （实测同一配置 4 并发出现 11.4s / 18.4s 两种结果）；
#   * 完全不 pkill 又会让 sleep 活 300s 并拖着 adb shell 一起等，探针算完了
#     却卡到 90s/180s 超时；
#   * 用 "sleep 300.<本脚本PID>" 让每个探针的 sleep 命令行唯一，pkill -f 精确
#     只杀自己那个，两边问题一起解决。
#   （试过 fifo 方案更干净，但 /data/local/tmp 上 mkfifo 被 SELinux 拒绝：
#     mkfifo: Permission denied）
SL="sleep 300.$$"
{ printf 'GET http://127.0.0.1:9126/big?n=%s HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n' "$N"; $SL; } \
  | timeout 75 nc $H $P > $O 2>/dev/null &
BG=$!
i=0; SZ=0
while [ $i -lt $LIM ]; do
  sleep 0.05
  SZ=$(wc -c < $O 2>/dev/null)
  [ -z "$SZ" ] && SZ=0
  [ "$SZ" -ge "$EXP" ] && break
  i=$((i+1))
done
E=$(now)
kill $BG 2>/dev/null
pkill -f "$SL" 2>/dev/null
# BYTES <本连接收到字节> <绝对起始ms> <绝对结束ms> <期望字节>
# 用绝对时间戳（/proc/uptime 毫秒）而不是相对 ms，父脚本才能算真正的重叠窗口
echo "BYTES $SZ $S $E $EXP"
rm -f $O
