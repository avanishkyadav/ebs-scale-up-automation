#!/usr/bin/env python3

from aws_cdk import core

from ebs_scale_up_automation.ebs_scale_up_automation_stack import EbsScaleUpAutomationStack


app = core.App()
EbsScaleUpAutomationStack(app, "ebs-scale-up-automation")

app.synth()
