// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/Helper.sol";
import "../src/Calculator.sol";

contract DeployScript is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");

        vm.startBroadcast(deployerPrivateKey);

        Helper helper = new Helper();
        Calculator calculator = new Calculator(address(helper));

        vm.stopBroadcast();

        console2.log("Helper deployed at:", address(helper));
        console2.log("Calculator deployed at:", address(calculator));
    }
}
