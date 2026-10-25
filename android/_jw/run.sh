#!/system/bin/sh
# 运行纯 JDK 回归单测（不需要 Android 设备、不需要模拟器）。
#
# 用法：在 weaknet/android/ 下执行  sh _jw/run.sh
#
# 为什么必须显式写 -sourcepath src：
#   _jw/com/weaknet/app/ 下只放测试类，被测代码一律从 src/ 编译。
#   历史上这里曾放着 Pipe.java / Profile.java / WeakNet.java 的陈旧副本，
#   一旦 _jw 目录被意外加入 classpath 或 sourcepath，测试就会静默跑在旧代码上
#   （实测那份副本比 src 落后多个版本，缺 v1.1.7 的中断补偿修复），
#   而测试照样全绿——比没有测试更坏。现在副本已删除，并显式锁定 sourcepath。
set -e
cd "$(dirname "$0")/.."

# android.jar 定位：优先用仓库自带的 sdk/，其次才看 ANDROID_HOME。
# 不能反过来——本机 ANDROID_HOME 常指向系统 SDK（没有 platforms 目录），
# 直接采信会报一堆「程序包 android.content 不存在」，看着像代码坏了。
AJAR=""
for CAND in "$(pwd)/sdk/platforms/android-34/android.jar" \
            "$(pwd)/sdk/platforms/android-33/android.jar" \
            "${ANDROID_HOME:-/nonexistent}/platforms/android-34/android.jar"; do
  if [ -f "$CAND" ]; then AJAR="$CAND"; break; fi
done
if [ -z "$AJAR" ]; then
  echo "找不到 android.jar。请把 Android SDK 放到 android/sdk/（需含 platforms/android-34），"
  echo "或设置 ANDROID_HOME 指向含 platforms 的 SDK。SDK 体积大，不入仓库。"
  exit 2
fi
echo "使用 android.jar: $AJAR"
OUT="_jw/out"
SRCPATH="src"

rm -rf "$OUT"
mkdir -p "$OUT"

echo "== 编译被测代码与测试类 =="
javac -encoding UTF-8 -nowarn \
      -cp "$AJAR" \
      -sourcepath "$SRCPATH" \
      -d "$OUT" \
      src/com/weaknet/app/*.java \
      _jw/com/weaknet/app/_*Test.java

FAIL=0
for T in _PortIdxTest _FlowMatchTest _ScopeTest _BucketConcTest; do
  echo ""
  echo "== $T =="
  if java -cp "$OUT;$AJAR" "com.weaknet.app.$T"; then
    :
  else
    echo "!! $T 未通过"
    FAIL=1
  fi
done

echo ""
if [ "$FAIL" = "0" ]; then
  echo "全部 JDK 单测通过"
else
  echo "存在未通过的单测"
fi
exit $FAIL
