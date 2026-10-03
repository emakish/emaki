import QtQuick
import Quickshell
import Quickshell.Io
import "Calculator.js" as Calculator

ShellRoot {
    IpcHandler {
        target: "tests"
        function arithmetic(): string {
            const cases = [["(2+3)*4", 20], ["2+3*4", 14], ["2^3^2", 512], ["-2^2", -4], ["(-2)^2", 4], ["2^-2", 0.25], ["1,5 + .5", 2], ["10%3", 1], ["1/3", 0.33333333], [" 1 + 2 ", 3], ["2++3", 5], ["0.1+0.2", 0.3], ["1/0", null], ["1%0", null], ["2^99999", null], ["Math.sin(1)", null], ["1; Qt.quit()", null], ["eval('1')", null], ["1+", null], ["(1+2", null], ["2**3", null], ["12", null], ["1.2.3+4", null], ["-".repeat(100) + "1", null], ["1+".repeat(600) + "1", null]];
            return JSON.stringify(cases.filter(test => Calculator.calculate(test[0]) !== test[1]).map(test => test[0]));
        }
    }
}
