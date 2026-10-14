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
set -e
cd "$(dirname "$0")"

# ---------- 1. 定位 JDK ----------
if [ -n "$JAVA_HOME" ] && [ -x "$JAVA_HOME/bin/javac" ]; then
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
  for c in "$LOCALAPPDATA/Android/Sdk" "$USERPROFILE/AppData/Local/Android/Sdk" \
           "$HOME/AppData/Local/Android/Sdk" "$HOME/Android/Sdk" "/usr/lib/android-sdk" "/opt/android-sdk"; do
    [ -d "$c" ] && AH="$c" && break
  done
fi
[ -n "$AH" ] || { echo "错误：找不到 Android SDK，请设置 ANDROID_HOME"; exit 1; }
echo "SDK: $AH"

# build-tools：取版本号最高的一个
if [ -z "$BT" ]; then
  BT="$(ls -d "$AH"/build-tools/*/ 2>/dev/null | sort -V | tail -1)"
  BT="${BT%/}"
fi
[ -d "$BT" ] || { echo "错误：找不到 build-tools，请安装 Android SDK Build-Tools 或设置 BT"; exit 1; }
echo "Build-Tools: $BT"

# android.jar：取 API 级别最高的 platform
if [ -z "$ANDROID_JAR" ]; then
  ANDROID_JAR="$(ls -d "$AH"/platforms/android-*/ 2>/dev/null | sort -V | tail -1)android.jar"
fi
[ -f "$ANDROID_JAR" ] || { echo "错误：找不到 android.jar，请安装 SDK Platform 或设置 ANDROID_JAR"; exit 1; }
echo "Platform: $ANDROID_JAR"

[ -n "$PY" ] || PY="$(command -v python3 || command -v python || true)"
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
"$JAVAC" -encoding UTF-8 -source 8 -target 8 \
  -bootclasspath "$ANDROID_JAR" -d build/classes src/com/weaknet/app/*.java 2>&1 | head -30

echo "==> 2. d8 -> dex"
rm -rf build/dex && mkdir -p build/dex
"$JAVA_CMD" -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 \
  --lib "$ANDROID_JAR" --min-api 24 --output build/dex \
  $(find build/classes -name '*.class' | tr '\n' ' ') 2>&1 | tail -5
ls -la build/dex

echo "==> 3. aapt2 编译资源（应用图标 mipmap/adaptive）"
rm -f build/res.zip
"$AAPT2" compile --dir res -o build/res.zip 2>&1 | tail -5

echo "==> 3b. aapt2 link"
rm -f build/base.apk
"$AAPT2" link -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
  -R build/res.zip \
  --min-sdk-version 24 --target-sdk-version 29 --version-code 1 --version-name 1.0 \
  -o build/base.apk 2>&1 | tail -10

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
print('apk entries:', zipfile.ZipFile(apk).namelist())
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
# 无 keystore 时自动生成一个自签名调试证书（首次构建会创建 weaknet.keystore，已被 .gitignore 排除）
if [ ! -f weaknet.keystore ]; then
  "$KEYTOOL" -genkeypair -v -keystore weaknet.keystore -alias weaknet \
    -keyalg RSA -keysize 2048 -validity 10950 -storepass weaknet123 -keypass weaknet123 \
    -dname "CN=WeakNet, OU=Dev, O=WeakNet, C=CN" >/dev/null 2>&1 || true
fi
rm -f WeakNet.apk
"$JAVA_CMD" -cp "$BT/lib/apksigner.jar" com.android.apksigner.ApkSignerTool sign \
  --ks weaknet.keystore --ks-key-alias weaknet \
  --ks-pass pass:weaknet123 --key-pass pass:weaknet123 --out WeakNet.apk build/aligned.apk

echo "==> 完成"
ls -la WeakNet.apk
"$JAVA_CMD" -cp "$BT/lib/apksigner.jar" com.android.apksigner.ApkSignerTool verify WeakNet.apk
