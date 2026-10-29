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
# Git Bash 下 java 不认 /c/... 形式的绝对路径（javac 认，java 不认），
# 不转的话 -cp 里的 android.jar 会被静默忽略：测试类一碰到 android.* 就
# NoClassDefFoundError，看着像代码坏了。Linux/macOS 没有 cygpath，原样使用。
if command -v cygpath >/dev/null 2>&1; then
  AJAR=$(cygpath -w "$AJAR")
fi
echo "使用 android.jar: $AJAR"
OUT="_jw/out"
SRCPATH="src"

# classpath 分隔符：Windows 是 ';'，Linux/macOS 是 ':'。
# 写死 ';' 的话，CI 的 linux runner 会把整串当成一个不存在的路径，
# 表现是「测试类全找不到」，看着像代码坏了。
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) CPSEP=";" ;;
  *) CPSEP=":" ;;
esac

rm -rf "$OUT"
mkdir -p "$OUT"

echo "== 编译被测代码与测试类 =="
# 分两阶段编译，踩过的坑记在这：
#   一次把 src/**/*.java 和测试类丢给 javac 时，只要被测代码里有一个类
#   （如 MyCombos）因 Android 依赖编不过，javac 会把它从符号表里剔掉，
#   连带同包里的纯逻辑类（ComboStep / ComboPlan）也一起「找不到符号」——
#   报错指向测试类，看着像测试写错了，实际是被测代码的锅。
#   纯逻辑类（ComboStep / ComboPlan）单独编一遍就没事：它们不碰任何 Android API。
#   第二阶段的 classpath 里的 $OUT 也要转成 Windows 路径：javac 是 Windows 版，
#   认 /c/... 这种 Unix 路径会当找不到，classpath 被静默忽略。
OUTW="$OUT"
if command -v cygpath >/dev/null 2>&1; then
  OUTW=$(cygpath -w "$OUT")
fi
javac -encoding UTF-8 -nowarn \
      -cp "$AJAR" \
      -d "$OUT" \
      src/com/weaknet/app/ComboStep.java \
      src/com/weaknet/app/Profile.java \
      src/com/weaknet/app/SpecGen.java \
      src/com/weaknet/app/ComboPlan.java

javac -encoding UTF-8 -nowarn \
      -cp "$AJAR$CPSEP$OUTW" \
      -sourcepath "$SRCPATH" \
      -d "$OUT" \
      src/com/weaknet/app/*.java \
      _jw/com/weaknet/app/_*Test.java

FAIL=0
for T in _PortIdxTest _FlowMatchTest _ScopeTest _BucketConcTest _PresetNotesTest _ParamNotesTest _NoteWrapTest _ComboNewAgainTest _PauseResumeTest _DelayLineTest; do
  echo ""
  echo "== $T =="
  if java -cp "$OUT$CPSEP$AJAR" "com.weaknet.app.$T"; then
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
