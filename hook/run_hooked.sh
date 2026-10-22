#!/bin/bash
# Build the USB logging hook and launch HOBOware's bundled x86_64 JVM with it injected.
# Log goes to captures/hook.log. Quit any running HOBOware first.
set -e
cd "$(dirname "$0")/.."
mkdir -p captures
clang -arch x86_64 -dynamiclib -O1 -Wno-deprecated-declarations \
    -framework IOKit -framework CoreFoundation \
    -include stdarg.h -include unistd.h -include stddef.h \
    -o hook/libhobohook.dylib hook/hook.c

APP=/Applications/HOBOware.app/Contents
JAVA_DIR="$APP/Resources/Java"
CP=$(plutil -extract NewJava.ClassPath json -o - "$APP/Info.plist" |
     python3 -c 'import sys,json; print(":".join(p.replace("$JAVAROOT", sys.argv[1]) for p in json.load(sys.stdin)))' "$JAVA_DIR")

: > captures/hook.log
export HOBO_HOOK_LOG="$PWD/captures/hook.log"
export DYLD_INSERT_LIBRARIES="$PWD/hook/libhobohook.dylib"
cd "$JAVA_DIR"
exec "$APP/PlugIns/jre.bundle/Contents/Home/jre/bin/java" \
    -Xmx1024m -Dfile.encoding=UTF-8 -Djava.library.path="$JAVA_DIR" \
    -cp "$CP" Appleseed.HOBOware
