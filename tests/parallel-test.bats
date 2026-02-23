#!/usr/bin/env bats
# Tests for scripts/parallel-test.sh argument validation.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../scripts" && pwd)/parallel-test.sh"

@test "parallel-test.sh --help shows usage" {
    run "$SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" =~ "Usage:" ]]
}

@test "parallel-test.sh -h shows usage" {
    run "$SCRIPT" -h
    [ "$status" -eq 0 ]
    [[ "$output" =~ "Usage:" ]]
}

@test "parallel-test.sh without -H fails" {
    run "$SCRIPT" n1-push
    [ "$status" -eq 2 ]
    [[ "$output" =~ "-H <host> is required" ]]
}

@test "parallel-test.sh without manifests fails" {
    run "$SCRIPT" -H mother
    [ "$status" -eq 2 ]
    [[ "$output" =~ "At least one manifest name is required" ]]
}

@test "parallel-test.sh with unknown option fails" {
    run "$SCRIPT" --bogus
    [ "$status" -eq 2 ]
    [[ "$output" =~ "Unknown option" ]]
}
