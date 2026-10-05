plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.laden.opsdash"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.laden.opsdash"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    val keystorePath = System.getenv("LDASH_KEYSTORE").orEmpty()
    val keystorePass = System.getenv("LDASH_KEYSTORE_PASS").orEmpty()
    val keyPass = System.getenv("LDASH_KEY_PASS").orEmpty()

    signingConfigs {
        if (keystorePath.isNotEmpty()) {
            create("laden") {
                storeFile = file(keystorePath)
                storePassword = keystorePass
                keyAlias = "laden-as"
                keyPassword = keyPass
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePath.isNotEmpty()) {
                signingConfig = signingConfigs.getByName("laden")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
}

val copyWeb = tasks.register<Copy>("copyWeb") {
    from(rootProject.file("../web"))
    into(layout.projectDirectory.dir("src/main/assets/web"))
    include("*.html", "*.css", "*.js")
}

tasks.named("preBuild").configure { dependsOn(copyWeb) }
