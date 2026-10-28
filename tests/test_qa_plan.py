"""Browser action contracts reject unsupported or unbounded plans before execution."""
import unittest
from ada_lab.qa import validate_plan


class QaPlanTests(unittest.TestCase):
    def plan(self,steps):
        return {'api_checks':[{'expected_status':200}],'browser_steps':steps}

    def test_keyboard_delay_settle_and_exact_limits_are_supported(self):
        steps=[{'op':'press','selector':'[data-testid=save]','value':'Enter'},
               {'op':'delay_next','target':'/api/repairs','ms':1},
               {'op':'delay_next','target':'/api/repairs','ms':2000},
               {'op':'settle'},
               {'op':'assert_count','selector':'[data-testid=error]:visible','count':0},
               {'op':'assert_count','selector':'div:has-text("ready")','count':1000}]
        validate_plan(self.plan(steps))
        plan=self.plan([{'op':'settle'}]*64);plan['api_checks']=[{'expected_status':200}]*30
        validate_plan(plan)

    def test_invalid_keyboard_count_and_delay_arguments_are_rejected(self):
        cases=[{'op':'press','selector':'button'},
               {'op':'press','selector':'button','value':42},
               {'op':'press','value':'Enter'},
               {'op':'delay_next','target':'https://example.invalid/api/repairs','ms':10},
               {'op':'delay_next','target':'/api/other','ms':10},
               {'op':'delay_next','target':'/api/repairs','ms':0},
               {'op':'delay_next','target':'/api/repairs','ms':2001},
               {'op':'delay_next','target':'/api/repairs','ms':True},
               {'op':'delay_next','target':'/api/repairs','ms':1.5},
               {'op':'assert_count','selector':'div','count':-1},
               {'op':'assert_count','selector':'div','count':1001},
               {'op':'assert_count','selector':'div','count':False},
               {'op':'assert_count','selector':'div','count':1.0},
               {'op':'assert_text','selector':'div','value':None},
               {'op':'execute_script','value':'untrusted code'}]
        for step in cases:
            with self.subTest(step=step),self.assertRaises(ValueError):validate_plan(self.plan([step]))

    def test_missing_empty_and_oversized_action_lists_are_rejected(self):
        for count in (0,65):
            with self.subTest(browser_count=count),self.assertRaises(ValueError):validate_plan(self.plan([{'op':'settle'}]*count))
        for count in (0,31):
            plan=self.plan([{'op':'settle'}]);plan['api_checks']=[{'expected_status':200}]*count
            with self.subTest(api_count=count),self.assertRaises(ValueError):validate_plan(plan)
