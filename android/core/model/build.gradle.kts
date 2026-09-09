plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    // JsonObject 是本模块公开 API 的一部分（Follower.extra），必须用 api 透传
    api(libs.kotlinx.serialization.json)
    testImplementation(libs.junit)
}
