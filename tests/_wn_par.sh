#!/system/bin/sh
# 用法: wn_par.sh <host> <port> <bytes_each> <conns>
# 设备内并发下载：并行跑 N 个探针，输出总字节与总墙钟。
# 必须在设备内并行（而不是从 PC 并发 adb shell）——adb shell 是串行排队，
# 从 PC 侧并发会被 adb 自己串行化，测出来的根本不是并发。
#
# 关键修正（2026-10-25 第二轮）：旧版所有子探针共用同一个输出文件
# /data/local/tmp/wn_out，且每个子探针启动时都 rm -f 它。并发时一个探针的
# wc -c 会读到别的探针写进去的字节，提前退出轮询 → 测量窗口被压缩，
# 表现为「并发度越高、测得的总带宽越高」（4 并发 1.15x、8 并发 1.29x）。
# 这是测量缺陷不是产品缺陷。现在每个子探针带自己的 tag，写独立文件。
#
# 口径：总字节 = 各探针实收之和；窗口 = max(结束) - min(起始)，
# 即「第一条传输开始到最后一条传输结束」的真实重叠跨度。
H=$1; P=$2; B=$3; N=$4
rm -f /data/local/tmp/par_*.txt /data/local/tmp/wn_out_*.bin
S=$(now)
i=1
while [ $i -le $N ]; do
  sh /data/local/tmp/wn_tun.sh $H $P $B $i > /data/local/tmp/par_$i.txt 2>/dev/null &
  i=$((i+1))
done
wait
E=$(now)
TOT=0; MIN=99999999999; MAX=0; BAD=0
i=1
while [ $i -le $N ]; do
  set -- $(awk '/^BYTES/{print $2, $3, $4, $5}' /data/local/tmp/par_$i.txt 2>/dev/null)
  V=${1:-0}; T0=${2:-0}; T1=${3:-0}; EX=${4:-0}
  TOT=$((TOT + V))
  [ "$V" -lt "$EX" ] && BAD=$((BAD+1))
  if [ "$T0" -gt 0 ] && [ "$T0" -lt "$MIN" ]; then MIN=$T0; fi
  if [ "$T1" -gt "$MAX" ]; then MAX=$T1; fi
  i=$((i+1))
done
if [ "$MAX" -le 0 ] || [ "$MIN" -ge "$MAX" ]; then
  # 时间戳异常时退回父脚本墙钟，保证总能产出数字
  WIN=$((E-S))
else
  WIN=$((MAX-MIN))
fi
echo "PAR $TOT $WIN $N $BAD"
