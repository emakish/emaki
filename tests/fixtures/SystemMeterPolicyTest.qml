pragma ComponentBehavior: Bound
import QtQml

// Exercise the production decision independently of software-renderer glassReady.
// The rendered integration also uses this component, rather than a duplicated test
// implementation of the policy.
QtObject {
    id: test
    property SystemMeterPolicy policy: SystemMeterPolicy {}
    function expect(cache: bool, separate: bool, state: string): void {
        if (policy.cacheEnabled !== cache || policy.separateMeter !== separate)
            throw new Error(state + ": cache=" + policy.cacheEnabled + " separate=" + policy.separateMeter);
    }
    Component.onCompleted: {
        expect(false, false, "closed");
        policy.opened = true;
        policy.soundPage = true;
        policy.meterVisible = true;
        policy.insideView = true;
        policy.insidePlate = true;
        expect(false, false, "flat fallback");
        policy.glassReady = true;
        expect(true, false, "opening sound retains cache before settled");
        policy.settled = true;
        expect(true, true, "settled sound separates meter");
        policy.settled = false;
        expect(true, false, "hover or rows moving retain cache");
        policy.settled = true;
        policy.insideView = false;
        expect(true, false, "scroller clipping retains cache with original meter path");
        policy.insideView = true;
        expect(true, true, "scroller settles");
        policy.insidePlate = false;
        expect(true, false, "rounded plate edge fallback");
        policy.insidePlate = true;
        policy.meterVisible = false;
        expect(true, false, "mic unavailable retains panel cache");
        policy.meterVisible = true;
        expect(true, true, "mic returns");
        policy.soundPage = false;
        expect(false, false, "page switch releases cache");
        policy.soundPage = true;
        expect(true, true, "return to sound");
        policy.opened = false;
        expect(false, false, "panel close releases cache");
        policy.opened = true;
        expect(true, true, "reopen sound");
        policy.glassReady = false;
        expect(false, false, "glass unavailable");
        console.log("SYSTEM_METER_POLICY_OK");
    }
}
