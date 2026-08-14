#!/usr/bin/env ruby

require "fileutils"
require "xcodeproj"

root = File.expand_path("..", __dir__)
project_path = File.join(root, "JarvisLife.xcodeproj")
abort("Project already exists at #{project_path}") if File.exist?(project_path)

project = Xcodeproj::Project.new(project_path)
project.root_object.attributes["LastSwiftUpdateCheck"] = "2700"
project.root_object.attributes["LastUpgradeCheck"] = "2700"

app_group = project.main_group.new_group("JarvisLife", "JarvisLife")
test_group = project.main_group.new_group("JarvisLifeTests", "JarvisLifeTests")

app_target = project.new_target(:application, "JarvisLife", :ios, "17.0")
test_target = project.new_target(:unit_test_bundle, "JarvisLifeTests", :ios, "17.0")
test_target.add_dependency(app_target)

source_names = %w[
  JarvisLifeApp.swift
  JarvisConfiguration.swift
  BiometricLock.swift
  ContentView.swift
  JarvisWebView.swift
  NativeIntegrationController.swift
  NativeSpeechController.swift
]

source_names.each do |name|
  reference = app_group.new_file(name)
  app_target.source_build_phase.add_file_reference(reference)
end

assets = app_group.new_file("Assets.xcassets")
app_target.resources_build_phase.add_file_reference(assets)
app_group.new_file("Info.plist")
app_group.new_file("JarvisLife.entitlements")

test_reference = test_group.new_file("JarvisConfigurationTests.swift")
test_target.source_build_phase.add_file_reference(test_reference)

project.build_configurations.each do |configuration|
  configuration.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] = "17.0"
end

app_target.build_configurations.each do |configuration|
  settings = configuration.build_settings
  settings["ASSETCATALOG_COMPILER_APPICON_NAME"] = "AppIcon"
  settings["ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME"] = "AccentColor"
  settings["CLANG_ENABLE_MODULES"] = "YES"
  settings["CODE_SIGN_STYLE"] = "Automatic"
  settings["CODE_SIGN_ENTITLEMENTS"] = "JarvisLife/JarvisLife.entitlements"
  settings["CURRENT_PROJECT_VERSION"] = "7"
  settings["DEVELOPMENT_TEAM"] = "NP9X453K55"
  settings["ENABLE_PREVIEWS"] = "YES"
  settings["ENABLE_USER_SCRIPT_SANDBOXING"] = "YES"
  settings["GENERATE_INFOPLIST_FILE"] = "NO"
  settings["INFOPLIST_FILE"] = "JarvisLife/Info.plist"
  settings["LD_RUNPATH_SEARCH_PATHS"] = ["$(inherited)", "@executable_path/Frameworks"]
  settings["MARKETING_VERSION"] = "0.1.0"
  settings["PRODUCT_BUNDLE_IDENTIFIER"] = "com.lextechnology.jarvislife"
  settings["PRODUCT_NAME"] = "$(TARGET_NAME)"
  settings["SUPPORTED_PLATFORMS"] = "iphoneos iphonesimulator"
  settings["SWIFT_EMIT_LOC_STRINGS"] = "YES"
  settings["SWIFT_VERSION"] = "5.0"
  settings["TARGETED_DEVICE_FAMILY"] = "1"
  settings["JARVIS_APP_URL"] = if configuration.name == "Debug"
    "http://127.0.0.1:8100/vida"
  else
    "https://jarvis-life.vercel.app/vida"
  end
  settings["APP_ATTEST_ENVIRONMENT"] = if configuration.name == "Debug"
    "development"
  else
    "production"
  end
end

test_target.build_configurations.each do |configuration|
  settings = configuration.build_settings
  settings["BUNDLE_LOADER"] = "$(TEST_HOST)"
  settings["CODE_SIGN_STYLE"] = "Automatic"
  settings["DEVELOPMENT_TEAM"] = "NP9X453K55"
  settings["GENERATE_INFOPLIST_FILE"] = "YES"
  settings["PRODUCT_BUNDLE_IDENTIFIER"] = "com.lextechnology.jarvislife.tests"
  settings["SWIFT_VERSION"] = "5.0"
  settings["TARGETED_DEVICE_FAMILY"] = "1"
  settings["TEST_HOST"] = "$(BUILT_PRODUCTS_DIR)/JarvisLife.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/JarvisLife"
end

scheme = Xcodeproj::XCScheme.new
scheme.add_build_target(app_target)
scheme.set_launch_target(app_target)
scheme.add_test_target(test_target)
scheme.save_as(project_path, "JarvisLife", true)

project.save
puts project_path
