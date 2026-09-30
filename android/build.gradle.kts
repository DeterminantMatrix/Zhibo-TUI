plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.ksp) apply false
}

// 工作区在 WPS 同步盘内，同步锁会破坏 build/ 目录（且用户要求工作区只留源码）。
// 构建输出统一重定向到同步目录之外；必须与源码同盘，否则 KSP/Room 生成代码
// 在跨盘符路径上做相对化会失败（different roots）。
allprojects {
    layout.buildDirectory.set(file("D:/zhibo-android-build/${name}"))
}

