#!/usr/bin/env bash
# WeakNet Android 手工构建脚本（无需 Gradle）
# 依赖：JDK 8+、Android SDK platform(android.jar)、build-tools(aapt2/d8/zipalign/apksigner)
#
# 用法：bash build.sh
# 路径可用环境变量覆盖（未设置时自动探测）：
#   JAVA_HOME          JDK 目录           例：C:/Program Files/Java/jdk-18.0.1
#   ANDROID_HOME       Android SDK 目录   例：C:/Users/you/AppData/Local/Android/Sdk
#   ANDROID_SDK_ROOT   同上（别名）
#   BT                 build-tools 目录（精确指定，优先级最高）
#   ANDROID_JAR        android.jar（精确指定，优先级最高）
#   PY                 python 解释器（仅用于把 dex 打进 apk，无 python 时自动退回 zip）
set -euo pipefail
cd "$(dirname "$0")"

# ---------- 0. 版本号 ----------
# 三处保持同值的约定（由 tests/_t_version.py 断言，不再靠人眼核对）：
#   weaknet/version.py 的 VERSION / BUILD（Windows 侧唯一真值）
#   AndroidManifest.xml 的 android:versionCode / versionName（aapt2 的输入）
#   下面两行的默认值（aapt2 link --replace-version 的输入）
# 环境变量可覆盖，方便打测试包；正式包不要覆盖。
VERSION_CODE="${VERSION_CODE:-120}"
VERSION_NAME="${VERSION_NAME:-1.1.10}"

# ---------- 1. 定位 JDK ----------
if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/javac" ]; then
  JAVAC="$JAVA_HOME/bin/javac"; JAVA_CMD="$JAVA_HOME/bin/java"; KEYTOOL="$JAVA_HOME/bin/keytool"
else
  JAVAC="$(command -v javac || true)"
  JAVA_CMD="$(command -v java || true)"
  KEYTOOL="$(command -v keytool || true)"
fi
[ -n "$JAVAC" ]   || { echo "错误：找不到 javac，请安装 JDK 并设置 JAVA_HOME"; exit 1; }
[ -n "$JAVA_CMD" ] || { echo "错误：找不到 java，请安装 JDK 并设置 JAVA_HOME"; exit 1; }

# ---------- 2. 定位 Android SDK ----------
AH="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$AH" ]; then
  # set -u 下未定义的变量会直接报错，这里逐个给空默认值
  for c in "${LOCALAPPDATA:-}/Android/Sdk" "${USERPROFILE:-}/AppData/Local/Android/Sdk" \
           "${HOME:-}/AppData/Local/Android/Sdk" "${HOME:-}/Android/Sdk" "/usr/lib/android-sdk" "/opt/android-sdk"; do
    [ -d "$c" ] && AH="$c" && break
  done
fi
[ -n "$AH" ] || { echo "错误：找不到 Android SDK，请设置 ANDROID_HOME"; exit 1; }
echo "SDK: $AH"

# build-tools：取版本号最高的一个
if [ -z "${BT:-}" ]; then
  BT="$(ls -d "$AH"/build-tools/*/ 2>/dev/null | sort -V | tail -1)"
  BT="${BT%/}"
fi
[ -d "$BT" ] || { echo "错误：找不到 build-tools，请安装 Android SDK Build-Tools 或设置 BT"; exit 1; }
echo "Build-Tools: $BT"

# android.jar：取 API 级别最高的 platform
if [ -z "${ANDROID_JAR:-}" ]; then
  ANDROID_JAR="$(ls -d "$AH"/platforms/android-*/ 2>/dev/null | sort -V | tail -1)android.jar"
fi
[ -f "$ANDROID_JAR" ] || { echo "错误：找不到 android.jar，请安装 SDK Platform 或设置 ANDROID_JAR"; exit 1; }
echo "Platform: $ANDROID_JAR"

if [ -z "${PY:-}" ]; then
  for _py in python python3; do
    if command -v "$_py" >/dev/null 2>&1 && "$_py" -c 'import sys' >/dev/null 2>&1; then
      PY="$(command -v "$_py")"; break
    fi
  done
fi
ZIP="$(command -v zip || true)"

# Windows 下工具带 .exe，统一探测一次
pick_exe() {
  local base="$1/$2"
  if [ -x "$base" ]; then echo "$base"; elif [ -x "$base.exe" ]; then echo "$base.exe"; else echo ""; fi
}
AAPT2="$(pick_exe "$BT" aapt2)"
ZIPALIGN="$(pick_exe "$BT" zipalign)"
[ -n "$AAPT2" ]    || { echo "错误：build-tools 里找不到 aapt2：$BT"; exit 1; }
[ -n "$ZIPALIGN" ] || { echo "错误：build-tools 里找不到 zipalign：$BT"; exit 1; }

echo "==> 0. 准备工作目录"
# 网页控制台已下线，不再需要 assets/dashboard.html；界面改为 App 原生 MainActivity
rm -rf assets
mkdir -p build/classes build/dex

echo "==> 1. javac"
rm -rf build/classes && mkdir -p build/classes
# 注意：不能把 javac 直接管到 head——javac 的告警输出含非 UTF-8 字节时 head 会判定
# 「二进制输入」而整段跳过，PIPESTATUS 才是真实退出码；早期版本因此把编译失败的
# 缺类 dex 打进了 APK，装到机器上必崩（ClassNotFoundException）。
if ! "$JAVAC" -encoding UTF-8 -source 8 -target 8 \
  -bootclasspath "$ANDROID_JAR" -d build/classes src/com/weaknet/app/*.java > build/javac.log 2>&1; then
  echo "错误：javac 失败，详见 build/javac.log"
  iconv -f UTF-8 -t UTF-8 -c build/javac.log 2>/dev/null | head -30 || head -30 build/javac.log
  exit 1
fi
# 关键类缺一个就退出，杜绝「缺类 dex 打进 APK」
for c in WeakNet VpnNetService TcpSession UdpSession ProxyServer CtrlServer MainActivity; do
  [ -f "build/classes/com/weaknet/app/$c.class" ] || { echo "错误：缺少 $c.class"; exit 1; }
done

echo "==> 2. d8 -> dex"
rm -rf build/dex && mkdir -p build/dex
if ! "$JAVA_CMD" -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 \
  --lib "$ANDROID_JAR" --min-api 24 --output build/dex \
  $(find build/classes -name '*.class' | tr '\n' ' ') > build/d8.log 2>&1; then
  echo "错误：d8 失败，详见 build/d8.log"
  tail -20 build/d8.log
  exit 1
fi
[ -s build/dex/classes.dex ] || { echo "错误：d8 没有产出 build/dex/classes.dex"; exit 1; }
ls -la build/dex

echo "==> 3. aapt2 编译资源（应用图标 mipmap/adaptive）"
rm -f build/res.zip
if ! "$AAPT2" compile --dir res -o build/res.zip > build/aapt_compile.log 2>&1; then
  echo "错误：aapt2 compile 失败，详见 build/aapt_compile.log"
  tail -20 build/aapt_compile.log
  exit 1
fi

echo "==> 3b. aapt2 link"
rm -f build/base.apk
if ! "$AAPT2" link -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
  -R build/res.zip \
    --min-sdk-version 24 --target-sdk-version 29 \
    --version-code "${VERSION_CODE}" --version-name "${VERSION_NAME}" \
    --replace-version \
    -o build/base.apk > build/aapt_link.log 2>&1; then
  echo "错误：aapt2 link 失败，详见 build/aapt_link.log"
  tail -20 build/aapt_link.log
  exit 1
fi
# 成品里真写进去的版本号必须等于构建参数：Manifest 写错时 --replace-version 会静默
# 用 Manifest 的值，装到机器上才发现版本不对，回滚时对不上号。
# 先落文件再取第一行：set -o pipefail 下 `aapt2 ... | head -1` 会因为 SIGPIPE
# 让整条管道失败，赋值语句的退出码非零 → set -e 直接退出，且什么提示都没有。
"$AAPT2" dump badging build/base.apk > build/badging.txt 2>/dev/null
BAG="$(sed -n '1p' build/badging.txt)"
case "$BAG" in
  *"versionCode='$VERSION_CODE'"*"versionName='$VERSION_NAME'"*) ;;
  *)
    echo "错误：成品 APK 的版本号与构建参数不一致"
    echo "  期望 versionCode=$VERSION_CODE versionName=$VERSION_NAME"
    echo "  实际 $BAG"
    exit 1
    ;;
esac

echo "==> 4. 注入 classes.dex"
if [ -n "$PY" ]; then
  "$PY" -c "
import zipfile
apk='build/base.apk'; dex='build/dex/classes.dex'
z=zipfile.ZipFile(apk,'a')
if 'classes.dex' in z.namelist():
    print('dex 已存在')
else:
    z.write(dex,'classes.dex',zipfile.ZIP_DEFLATED)
z.close()
names = zipfile.ZipFile(apk).namelist()
print('apk entries:', names)
if 'classes.dex' not in names:
    raise SystemExit('错误：APK 里没有 classes.dex')
"
elif [ -n "$ZIP" ]; then
  cp -f build/dex/classes.dex . 2>/dev/null || true
  "$ZIP" -q -j build/base.apk classes.dex && rm -f classes.dex
else
  echo "错误：需要 python 或 zip 才能把 dex 打进 apk"; exit 1
fi

echo "==> 5. zipalign"
rm -f build/aligned.apk
"$ZIPALIGN" -p -f 4 build/base.apk build/aligned.apk

echo "==> 6. 签名"
# 签名口令不入库：
#   优先读环境变量 KS_PASS；否则读本地文件 weaknet.keystore.pass（已被 .gitignore 排除）。
#   两者都没有时 —— 若 keystore 也不存在，就随机生成一个口令并写进那个文件；
#   若 keystore 已存在却拿不到口令，直接报错（不猜、不试默认口令）。
#   口令写在仓库里等于把签名私钥的访问方式公开出去，哪怕 keystore 本身没提交。
# apksigner 的主类包名随 build-tools 版本变过
#   （34.x 是 com.android.apksigner，早期是 com.android.tools.apksigner），
# 写死一个会在换版本时直接 ClassNotFound。这里探测一次，两种都认。
APKSIGNER_CLS=""
for _c in com.android.apksigner.ApkSignerTool com.android.tools.apksigner.ApkSignerTool; do
  if "$JAVA_CMD" -cp "$BT/lib/apksigner.jar" "$_c" --version >/dev/null 2>&1; then
    APKSIGNER_CLS="$_c"; break
  fi
done
[ -n "$APKSIGNER_CLS" ] || { echo "错误：$BT/lib/apksigner.jar 里找不到 apksigner 主类"; exit 1; }
echo "  apksigner 主类：$APKSIGNER_CLS"
KS_PASS_FILE="weaknet.keystore.pass"
if [ -n "${KS_PASS:-}" ]; then
  KS_PASS="$KS_PASS"
elif [ -f "$KS_PASS_FILE" ]; then
  KS_PASS="$(tr -d '\r\n' < "$KS_PASS_FILE")"
else
  KS_PASS=""
fi

if [ ! -f weaknet.keystore ]; then
  if [ -z "$KS_PASS" ]; then
    # 首次构建：随机口令，十六进制无特殊字符，避免 apksigner 的 pass: 解析歧义
    KS_PASS="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    printf '%s' "$KS_PASS" > "$KS_PASS_FILE"
    echo "  新建签名口令，已写入 $KS_PASS_FILE（该文件不入库，请勿丢失）"
  fi
  "$KEYTOOL" -genkeypair -v -keystore weaknet.keystore -alias weaknet \
    -keyalg RSA -keysize 2048 -validity 10950 -storepass "$KS_PASS" -keypass "$KS_PASS" \
    -dname "CN=WeakNet, OU=Dev, O=WeakNet, C=CN" >/dev/null 2>&1 || true
elif [ -z "$KS_PASS" ]; then
  echo "错误：weaknet.keystore 已存在但拿不到口令。"
  echo "      请设置环境变量 KS_PASS，或把口令写进 android/$KS_PASS_FILE（一行，无换行）。"
  exit 1
fi
rm -f WeakNet.apk
"$JAVA_CMD" -cp "$BT/lib/apksigner.jar" "$APKSIGNER_CLS" sign \
  --ks weaknet.keystore --ks-key-alias weaknet \
  --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" --out WeakNet.apk build/aligned.apk

echo "==> 完成"
ls -la WeakNet.apk
"$JAVA_CMD" -cp "$BT/lib/apksigner.jar" "$APKSIGNER_CLS" verify WeakNet.apk

# 记录「内容指纹」：APK 里的签名块带构建时刻，同一份源码两次构建的
# SHA-256 必然不同（ZIP 条目时间戳变了）。所以对外只公布内容指纹 ——
# 把所有条目按 CRC 排序后哈希，与构建时间无关，才是真正能用来核对
# 「装的和我发的是同一个」的凭据。
echo "==> 内容指纹（与构建时刻无关）"
if command -v python >/dev/null 2>&1; then
  python - <<'PYEOF'
import hashlib, zipfile
z = zipfile.ZipFile("WeakNet.apk")
lines = sorted("%08x  %s" % (i.CRC, i.filename) for i in z.infolist())
h = hashlib.sha256("\n".join(lines).encode()).hexdigest()
print("  content-sha256: %s" % h)
PYEOF
else
  echo "  (无 python，跳过内容指纹计算)"
fi
