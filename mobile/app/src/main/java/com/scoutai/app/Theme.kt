package com.scoutai.app

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Dark = darkColorScheme(
    primary = Color(0xFF818CF8), onPrimary = Color(0xFF1E1B4B),
    primaryContainer = Color(0xFF312E81), onPrimaryContainer = Color(0xFFE0E7FF),
    secondaryContainer = Color(0xFF232634), onSecondaryContainer = Color(0xFFD7DAE6),
    background = Color(0xFF0B0D12), onBackground = Color(0xFFE6E8EF),
    surface = Color(0xFF0B0D12), onSurface = Color(0xFFE6E8EF),
    surfaceContainerLow = Color(0xFF11131A), surfaceContainer = Color(0xFF151822),
    surfaceContainerHigh = Color(0xFF1B1F2B), surfaceContainerHighest = Color(0xFF222737),
    onSurfaceVariant = Color(0xFF9AA1B5), outlineVariant = Color(0xFF2A2F40),
)
private val Light = lightColorScheme(
    primary = Color(0xFF4F46E5), onPrimary = Color.White,
    primaryContainer = Color(0xFFE0E7FF), onPrimaryContainer = Color(0xFF1E1B4B),
    secondaryContainer = Color(0xFFE9EBF5), onSecondaryContainer = Color(0xFF2B3040),
    background = Color(0xFFF8F9FC), surface = Color(0xFFF8F9FC),
    surfaceContainerLow = Color(0xFFF1F3F9), surfaceContainer = Color(0xFFFFFFFF),
)

@Composable
fun ScoutTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val scheme = if (Build.VERSION.SDK_INT >= 31) {
        val ctx = LocalContext.current
        if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
    } else if (dark) Dark else Light
    MaterialTheme(colorScheme = scheme, content = content)
}

val Red = Color(0xFFEF4444)
val Amber = Color(0xFFF59E0B)
val Green = Color(0xFF22C55E)
val Blue = Color(0xFF60A5FA)
