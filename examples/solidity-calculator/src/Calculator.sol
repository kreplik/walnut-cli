// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./Helper.sol";

contract Calculator {
    IHelper public helper;

    constructor(address _helper) {
        helper = IHelper(_helper);
    }

    function compute(uint256 a, uint256 b) external returns (uint256 result) {
        uint256 pre = a * b;                   
        uint256 ext = helper.getExternalValue(pre);
        result = pre + ext + 100;
    }
}