// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IHelper {
    function getExternalValue(uint256 base) external returns (uint256);
}

contract Helper is IHelper {
    function getExternalValue(uint256 base) external returns (uint256) {
        return (base % 7) + 42;
    }
}