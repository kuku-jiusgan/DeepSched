import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import ScheduleRule
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_soft_constraints import sibling_cohesion_weight


class ScheduleRuleSiblingCohesionTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_rule_is_created_with_default_weight(self):
        rule = get_solver_constraints(self.db)["sibling_task_cohesion"]

        self.assertTrue(rule.is_enabled)
        self.assertFalse(rule.params["strict"])
        self.assertEqual(1.0, rule.params["weight"])
        self.assertEqual(100, sibling_cohesion_weight(rule))

    def test_disabled_rule_has_no_objective_weight(self):
        get_solver_constraints(self.db)
        rule = self.db.query(ScheduleRule).filter(
            ScheduleRule.code == "sibling_task_cohesion",
        ).one()
        rule.is_enabled = False

        self.assertEqual(0, sibling_cohesion_weight(rule))


if __name__ == "__main__":
    unittest.main()
