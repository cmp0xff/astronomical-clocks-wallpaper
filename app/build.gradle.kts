import dev.detekt.gradle.Detekt
import dev.detekt.gradle.extensions.FailOnSeverity
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.androidApplication)
    alias(libs.plugins.detekt)
}

android {
    namespace = "io.github.godaniya.astronomicalclockswallpaper"
    compileSdk = 37
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "io.github.godaniya.astronomicalclockswallpaper"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isIncludeAndroidResources = true
    }

    lint {
        checkAllWarnings = true
        warningsAsErrors = true
        abortOnError = true
        checkTestSources = true
    }
}

androidComponents {
    beforeVariants { variantBuilder ->
        variantBuilder.hostTests.getValue("UnitTest").enable = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
        allWarningsAsErrors = true
        freeCompilerArgs.addAll(
            "-Wextra",
            "-Xjsr305=strict",
            "-Xjspecify-annotations=strict",
            "-Xnullability-annotations=@org.jetbrains.annotations:strict," +
                "@androidx.annotation:strict,@android.annotation:strict",
            "-Xreturn-value-checker=full",
            "-Xrender-internal-diagnostic-names",
        )
    }
}

dependencies {
    implementation(libs.kotlinStdlib)
    // Astronomy Engine (MIT), pinned to the commit that tag v2.1.19 points at. JitPack
    // builds it on demand and caches the result per revision; the artifact is compiled
    // with Kotlin 1.6.10 metadata, which this project's pinned compiler reads.
    implementation(libs.astronomyEngine)
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    detektPlugins(libs.detektKtlintWrapper)
    add("kotlinCompilerClasspath", libs.kotlinCompilerEmbeddable)
}

detekt {
    toolVersion = libs.versions.detekt.get()
    buildUponDefaultConfig = true
    allRules = true
    config.setFrom(rootProject.files("config/detekt/detekt.yml"))
    failOnSeverity = FailOnSeverity.Warning
    ignoreFailures = false
}

tasks.withType<Detekt>().configureEach {
    jvmTarget = "17"
    exclude("**/build/**")
}

tasks.withType<Test>().configureEach {
    systemProperty("robolectric.dependency.repo.url", "https://repo.maven.apache.org/maven2")
    // Robolectric 4.17 reflects into JDK internals (e.g. jdk.internal.access.SharedSecrets);
    // open the modules it needs so tests run on JDK 17+ instead of failing with
    // IllegalAccessException. See https://robolectric.org/getting-started/.
    jvmArgs(
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        "--add-opens=java.base/java.net=ALL-UNNAMED",
        "--add-opens=java.base/java.security=ALL-UNNAMED",
        "--add-opens=java.base/java.text=ALL-UNNAMED",
        "--add-opens=java.base/jdk.internal.access=ALL-UNNAMED",
        "--add-opens=java.desktop/java.awt.font=ALL-UNNAMED",
        "--add-opens=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED",
    )
    if (name != "exportRepresentativeImages") {
        filter {
            excludeTestsMatching("*OrlojRepresentativeExport*")
        }
    }
}

tasks.register<Test>("exportRepresentativeImages") {
    description = "Exports representative Orloj dial Canvas PNGs to build/reports/orloj."
    group = "verification"
    outputs.dir(layout.buildDirectory.dir("reports/orloj"))
    // Read the unit-test classes and runtime classpath off the configured test task rather than
    // mapping a provider through it. `testTask.map { it.testClassesDirs }` registers the test task as
    // a producer, so invoking the export would run the entire unit suite first; taking the
    // collections keeps the compilation dependencies without the execution one.
    val unitTest = tasks.named<Test>("testDebugUnitTest").get()
    testClassesDirs = unitTest.testClassesDirs
    classpath = unitTest.classpath
    filter {
        setIncludePatterns("io.github.godaniya.astronomicalclockswallpaper.OrlojRepresentativeExport.*")
    }
}
